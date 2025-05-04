import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
import os
import re
from datetime import datetime

# Command configuration
DISPLAY_NAME = "Game Stats"
DESCRIPTION = "Shows your THE FINALS leaderboard stats (tournament wins, fans, and cashouts)"
ENABLED_BY_DEFAULT = True  # Enable by default for convenience

# API constants
API_BASE_URL = "https://api.the-finals-leaderboard.com/v1/leaderboard"
DEFAULT_PLATFORM = "crossplay"

# Stats endpoints
ENDPOINTS = {
    "tournaments": "/the-finals/",
    "sponsors": "/s6sponsor/",
    "worldtour": "/s6worldtour/",
}

async def fetch_player_stats(session, in_game_name):
    """
    Fetch player stats from all three leaderboards
    """
    stats = {
        "tournaments": None,
        "sponsors": None, 
        "worldtour": None
    }
    
    # Normalize the name for case-insensitive comparison
    normalized_name = in_game_name.lower()
    
    # Fetch data from each endpoint
    for stat_type, endpoint in ENDPOINTS.items():
        url = f"{API_BASE_URL}{endpoint}{DEFAULT_PLATFORM}"
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Look for the player in the data (case-insensitive)
                    for player in data.get("data", []):
                        if player.get("name", "").lower() == normalized_name:
                            stats[stat_type] = player
                            break
        except Exception as e:
            print(f"Error fetching {stat_type} stats: {e}")
    
    return stats

def create_stats_embed(user, in_game_name, stats):
    """Create an embed with the player's stats in a vertical format"""
    embed = discord.Embed(
        title=f"THE FINALS Stats for {in_game_name}",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )
    
    # Add user avatar if available
    if user.avatar:
        embed.set_author(name=user.display_name, icon_url=user.avatar.url)
    else:
        embed.set_author(name=user.display_name)
    
    # Get club tag if available
    club_tag = None
    for data in stats.values():
        if data and "clubTag" in data and data["clubTag"]:
            club_tag = data["clubTag"]
            break
    
    # Add player name with club tag if available
    player_header = f"{in_game_name}"
    if club_tag:
        player_header = f"[{club_tag}] {in_game_name}"
    
    embed.add_field(
        name="Player",
        value=player_header,
        inline=False
    )
    
    # Tournament stats - vertically displayed
    tournament_data = stats["tournaments"]
    if tournament_data:
        embed.add_field(
            name="🏆 Tournament Wins",
            value=f"**Rank:** #{tournament_data.get('rank', 'N/A')}\n"
                  f"**Wins:** {tournament_data.get('tournamentWins', 0)}",
            inline=False
        )
    else:
        embed.add_field(
            name="🏆 Tournament Wins",
            value="No data found",
            inline=False
        )
    
    # Sponsor stats - vertically displayed
    sponsor_data = stats["sponsors"]
    if sponsor_data:
        sponsor_value = f"**Rank:** #{sponsor_data.get('rank', 'N/A')}\n"
        sponsor_value += f"**Fans:** {sponsor_data.get('fans', 0)}"
        
        # Add sponsor if available
        if sponsor_data.get('sponsor'):
            sponsor_value += f"\n**Sponsor:** {sponsor_data.get('sponsor')}"
        
        embed.add_field(
            name="👥 Sponsor Fans",
            value=sponsor_value,
            inline=False
        )
    else:
        embed.add_field(
            name="👥 Sponsor Fans",
            value="No data found",
            inline=False
        )
    
    # World Tour stats - vertically displayed
    worldtour_data = stats["worldtour"]
    if worldtour_data:
        embed.add_field(
            name="💰 World Tour Cashouts",
            value=f"**Rank:** #{worldtour_data.get('rank', 'N/A')}\n"
                  f"**Cashouts:** {worldtour_data.get('cashouts', 0)}",
            inline=False
        )
    else:
        embed.add_field(
            name="💰 World Tour Cashouts",
            value="No data found",
            inline=False
        )
    
    # Add player handles if available - placed at the bottom
    handles = []
    for platform in ["steamName", "psnName", "xboxName"]:
        for data in stats.values():
            if data and platform in data and data[platform]:
                platform_name = platform.replace("Name", "").title()
                handles.append(f"**{platform_name}:** {data[platform]}")
                break
    
    if handles:
        embed.add_field(
            name="Platform Handles",
            value="\n".join(handles),
            inline=False
        )
    
    embed.set_footer(text="Data from THE FINALS Leaderboard")
    
    # Add a nice thumbnail
    embed.set_thumbnail(url="https://media.discordapp.net/attachments/774716444952494080/1358069564331524138/image.png?ex=67f28057&is=67f12ed7&hm=1ef10f0f720bf6d54ea1fc07936d44d6ef5e6681a9912fe28751cb0eb5c7a656&=&format=webp&quality=lossless")  # THE FINALS logo
    
    return embed

async def setup(bot: commands.Bot) -> None:
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    # Import get_user_link function directly
    from command_link_setup import get_user_link
    
    @bot.tree.command(name="gamestats", description="View your THE FINALS leaderboard statistics")
    async def gamestats(interaction: discord.Interaction):
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Get user's linked account
        user_link = await get_user_link(interaction.user.id)
        
        if not user_link:
            await interaction.response.send_message(
                "You don't have a linked THE FINALS account. Please use the Verify button in the verification channel or the `/delete_link` command to link your account.",
                ephemeral=True
            )
            return
        
        in_game_name = user_link.get("in_game_name")
        await interaction.response.defer(thinking=True)
        
        # Create aiohttp session and fetch stats
        async with aiohttp.ClientSession() as session:
            stats = await fetch_player_stats(session, in_game_name)
        
        # Check if we found any data
        any_data_found = any(stats.values())
        
        if any_data_found:
            embed = create_stats_embed(interaction.user, in_game_name, stats)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(
                f"No statistics found for {in_game_name} in THE FINALS leaderboards. If you recently changed your name, please update your linked account using the `/delete_link` command and verify again.",
                ephemeral=True
            )
    
    # Optional: Add a command to look up another player's stats
    @bot.tree.command(name="lookup", description="Look up THE FINALS stats for any player")
    @app_commands.describe(player_name="The player's THE FINALS name (format: name#0000)")
    async def lookup_stats(interaction: discord.Interaction, player_name: str):
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        await interaction.response.defer(thinking=True)
        
        # Create aiohttp session and fetch stats
        async with aiohttp.ClientSession() as session:
            stats = await fetch_player_stats(session, player_name)
        
        # Check if we found any data
        any_data_found = any(stats.values())
        
        if any_data_found:
            embed = create_stats_embed(interaction.user, player_name, stats)
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(
                f"No statistics found for {player_name} in THE FINALS leaderboards. Make sure you've entered the exact in-game name (format: name#0000).",
                ephemeral=True
            )