import os
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
import re
from azure.cosmos import CosmosClient, PartitionKey, exceptions

DISPLAY_NAME = "Check Link"
DESCRIPTION = "Allows moderators to check linked accounts between Discord and in-game names."
ENABLED_BY_DEFAULT = False

# Use environment variables if available; otherwise, fall back to default values.
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT") 
COSMOS_KEY = os.getenv("COSMOS_KEY")
COSMOS_DATABASE = os.getenv("COSMOS_DATABASE")

async def get_link_by_discord(discord_id: int):
    """
    Find a user's link by their Discord ID.
    Returns the link document or None if not found.
    """
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
        container = database.create_container_if_not_exists(
            id="user_links",
            partition_key=PartitionKey(path="/discord_id"),
            offer_throughput=400
        )

        # Query to find the most recent link for this Discord ID
        query = "SELECT TOP 1 * FROM c WHERE c.discord_id = @discord_id ORDER BY c.timestamp DESC"
        parameters = [{"name": "@discord_id", "value": discord_id}]
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        if items:
            return items[0]
        return None
    except Exception as e:
        print(f"Error retrieving link by Discord ID: {e}")
        return None

async def get_link_by_ingame_name(in_game_name: str):
    """
    Find a user's link by their in-game name.
    Returns the link document or None if not found.
    """
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=COSMOS_DATABASE)
        container = database.create_container_if_not_exists(
            id="user_links",
            partition_key=PartitionKey(path="/discord_id"),
            offer_throughput=400
        )

        # Query to find the link by in-game name
        query = "SELECT TOP 1 * FROM c WHERE c.in_game_name = @in_game_name ORDER BY c.timestamp DESC"
        parameters = [{"name": "@in_game_name", "value": in_game_name}]
        
        items = list(container.query_items(
            query=query,
            parameters=parameters,
            enable_cross_partition_query=True
        ))
        
        if items:
            return items[0]
        return None
    except Exception as e:
        print(f"Error retrieving link by in-game name: {e}")
        return None

# Check if input matches in-game name format (name#0000)
def is_ingame_name_format(name: str) -> bool:
    return bool(re.match(r'^.+#\d{4}$', name))

async def setup(bot: commands.Bot) -> None:
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    @bot.tree.command(name="checklink", description="Check the link between a Discord user and in-game name.")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.describe(
        identifier="Enter either a Discord user mention or in-game name (format: name#0000)"
    )
    async def checklink(interaction: discord.Interaction, identifier: str):
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Check if the user has the required permission
        if not interaction.channel.permissions_for(interaction.user).manage_channels:
            await interaction.response.send_message(
                "You need the 'Manage Channels' permission to use this command.",
                ephemeral=True
            )
            return

        # Initial response to show we're processing
        await interaction.response.defer(ephemeral=True)
        
        # Determine if the input is a Discord user mention or an in-game name
        discord_user = None
        discord_id = None
        
        # Check if input is a Discord user mention
        if identifier.startswith('<@') and identifier.endswith('>'):
            # Extract the ID from the mention
            try:
                mention_id = int(identifier.strip('<@!&>'))
                discord_id = mention_id
                discord_user = await bot.fetch_user(mention_id)
            except (ValueError, discord.errors.NotFound):
                await interaction.followup.send(f"Could not find a Discord user with the ID extracted from: {identifier}", ephemeral=True)
                return
            
            # Get link by Discord ID
            link_data = await get_link_by_discord(discord_id)
            
            if link_data:
                embed = discord.Embed(
                    title="Link Found",
                    color=discord.Color.green()
                )
                embed.add_field(name="Discord User", value=f"{link_data['discord_name']} (<@{link_data['discord_id']}>)", inline=False)
                embed.add_field(name="In-Game Name", value=link_data['in_game_name'], inline=False)
                embed.add_field(name="Linked On", value=link_data['timestamp'], inline=False)
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(f"No link found for Discord user: {discord_user.name}", ephemeral=True)
            
        # If it's not a mention, check if it matches in-game name format
        elif is_ingame_name_format(identifier):
            # Search by in-game name
            link_data = await get_link_by_ingame_name(identifier)
            
            if link_data:
                embed = discord.Embed(
                    title="Link Found",
                    color=discord.Color.green()
                )
                embed.add_field(name="Discord User", value=f"{link_data['discord_name']} (<@{link_data['discord_id']}>)", inline=False)
                embed.add_field(name="In-Game Name", value=link_data['in_game_name'], inline=False)
                embed.add_field(name="Linked On", value=link_data['timestamp'], inline=False)
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(f"No link found for in-game name: {identifier}", ephemeral=True)
        else:
            # Try to interpret as a Discord username
            try:
                # Try to find users in the guild by name
                if interaction.guild:
                    members = [member for member in interaction.guild.members if identifier.lower() in member.name.lower() or (member.nick and identifier.lower() in member.nick.lower())]
                    
                    if len(members) == 1:
                        discord_user = members[0]
                        discord_id = discord_user.id
                        
                        # Get link by Discord ID
                        link_data = await get_link_by_discord(discord_id)
                        
                        if link_data:
                            embed = discord.Embed(
                                title="Link Found",
                                color=discord.Color.green()
                            )
                            embed.add_field(name="Discord User", value=f"{link_data['discord_name']} (<@{link_data['discord_id']}>)", inline=False)
                            embed.add_field(name="In-Game Name", value=link_data['in_game_name'], inline=False)
                            embed.add_field(name="Linked On", value=link_data['timestamp'], inline=False)
                            await interaction.followup.send(embed=embed, ephemeral=True)
                        else:
                            await interaction.followup.send(f"No link found for Discord user: {discord_user.name}", ephemeral=True)
                        return
                    elif len(members) > 1:
                        # Found multiple users, ask for clarification
                        user_list = "\n".join([f"{member.name}#{member.discriminator}" for member in members[:10]])
                        if len(members) > 10:
                            user_list += f"\n...and {len(members) - 10} more"
                        await interaction.followup.send(f"Found multiple users matching '{identifier}'. Please be more specific or use a mention:\n{user_list}", ephemeral=True)
                        return
                
                # If we reach here, the input format is invalid or no user was found
                await interaction.followup.send(
                    "Invalid format. Please use either:\n"
                    "- A Discord user mention (@username)\n"
                    "- An in-game name with format 'name#0000'", 
                    ephemeral=True
                )
            except Exception as e:
                await interaction.followup.send(f"An error occurred while processing your request: {str(e)}", ephemeral=True)