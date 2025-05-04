import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import datetime
from io import BytesIO
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from azure.cosmos import CosmosClient, PartitionKey, exceptions
import aiohttp
import urllib.parse
import time

DISPLAY_NAME = "Rank History"
DESCRIPTION = "Shows your THE FINALS rank history graph over time"
ENABLED_BY_DEFAULT = True  # Whether this feature is enabled by default

# API endpoints
BASE_URL = "https://thefinals.fortunevale.de/api"
CURRENT_SEASON = "s6"  # Update as needed or fetch dynamically

# Default number of days to display in history
DEFAULT_DAYS = 30

# Color scheme (Dark Mode) - Premium cohesive design
BACKGROUND_COLOR = "#0F1218"      # Deep navy-black background
PRIMARY_COLOR = "#5A9BF2"         # Soft azure blue (main line)
SECONDARY_COLOR = "#66D9E8"       # Light cyan (comparison line)
ACCENT_COLOR = "#9D80F2"          # Soft purple for highlights (replacing red)
TEXT_COLOR = "#E2E8F0"            # Light silver-blue text
GRID_COLOR = "#2D3748"            # Dark blue-gray for grid lines

# League Colors
LEAGUE_COLORS = {
    "Bronze": "#CD7F32",
    "Silver": "#C0C0C0",
    "Gold": "#FFD700",
    "Platinum": "#E5E4E2", 
    "Diamond": "#B9F2FF",
    "Ruby": "#FF0000"
}

# Mapping for leagueNumber to league name
LEAGUE_MAPPING = {
    1: "Bronze",
    2: "Silver",
    3: "Gold",
    4: "Platinum",
    5: "Diamond",
    6: "Ruby"
}

# Cosmos DB Configuration - only needed for user links
COSMOS_ENDPOINT = ""
COSMOS_KEY = ""
DB_NAME = ""

async def get_player_data_from_api(player_name: str) -> dict:
    """Fetch player data from the leaderboard API."""
    try:
        encoded_name = urllib.parse.quote(player_name)
        url = f"{BASE_URL}/leaderboard/name/{CURRENT_SEASON}/{encoded_name}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 404:
                    print(f"Player '{player_name}' not found in season {CURRENT_SEASON}")
                    return None
                else:
                    print(f"API Error: {response.status} - {await response.text()}")
                    return None
    except Exception as e:
        print(f"Error fetching player data: {e}")
        return None

async def get_player_seasons(player_name: str) -> list:
    """Get the seasons a player has participated in."""
    try:
        encoded_name = urllib.parse.quote(player_name)
        url = f"{BASE_URL}/leaderboard/listSeasons/{encoded_name}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    return []
    except Exception as e:
        print(f"Error fetching player seasons: {e}")
        return []

def map_score_to_league(score: int, placement: int = None) -> int:
    """
    Map score to league number based on thresholds.
    Ruby (6) is only assigned to top 500 players regardless of score.
    """
    # Special case: Ruby league is based on placement, not score
    if placement is not None and placement <= 500:
        return 6  # Ruby
    
    # Handle None score - default to Bronze
    if score is None:
        return 1  # Bronze
        
    # Otherwise, use score thresholds
    if score >= 40000:
        return 5  # Diamond
    elif score >= 30000:
        return 4  # Platinum
    elif score >= 20000:
        return 3  # Gold
    elif score >= 10000:
        return 2  # Silver
    else:
        return 1  # Bronze

async def convert_history_to_rank_entries(history_data: dict) -> list:
    """Convert the API history format to rank entries for plotting."""
    if not history_data or 'History' not in history_data or 'CurrentPlacement' not in history_data:
        return []
    
    # Extract score history
    score_history = history_data['History'].get('s', {})
    if not score_history:  # If we have no score history
        return []
        
    rank_entries = []
    
    for minutes_str, score in score_history.items():
        # Convert minutes to timestamp with explicit timezone
        minutes = int(minutes_str)
        timestamp = datetime.datetime.fromtimestamp(minutes * 60, tz=datetime.timezone.utc)
        
        # Get placement at this point if available
        placement = None
        if 'p' in history_data['History']:
            # Find the closest placement entry
            placement_times = [int(t) for t in history_data['History']['p'].keys()]
            closest_time = min(placement_times, key=lambda x: abs(x - minutes), default=None)
            if closest_time and abs(closest_time - minutes) < 60:  # Within an hour
                placement = history_data['History']['p'].get(str(closest_time))
        
        # Ensure score is properly converted to int or None
        try:
            clean_score = int(score) if score is not None else None
        except (ValueError, TypeError):
            clean_score = None
        
        # Create entry
        league_number = map_score_to_league(clean_score, placement)
        entry = {
            'name': history_data['CurrentPlacement']['PlayerName'],
            'rankScore': clean_score,
            'rank': placement,  # May be None
            'timestamp': timestamp.isoformat(),
            'leagueNumber': league_number,
            'league': LEAGUE_MAPPING.get(league_number, "Unknown"),
            'steamName': history_data['CurrentPlacement'].get('SteamName', ''),
            'xboxName': history_data['CurrentPlacement'].get('XboxName', ''),
            'psnName': history_data['CurrentPlacement'].get('PlaystationName', ''),
        }
        rank_entries.append(entry)
    
    # Sort by timestamp
    rank_entries.sort(key=lambda x: x['timestamp'])
    return rank_entries

async def get_user_link(discord_id: int) -> dict:
    """Get the most recent link for a specific Discord user from Cosmos DB."""
    try:
        client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
        database = client.create_database_if_not_exists(id=DB_NAME)
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
            return items[0]  # Return the most recent link
        return None
    except Exception as e:
        print(f"Error retrieving user link: {e}")
        return None

async def get_player_rank_history(in_game_name: str, days: int = DEFAULT_DAYS) -> list:
    """Get the rank history for a player from the external API."""
    # This function now uses the new API instead of Cosmos DB
    player_data = await get_player_data_from_api(in_game_name)
    
    if not player_data:
        print(f"No data found for player {in_game_name} in API")
        return []
    
    # Convert the history data to the format expected by your graph function
    rank_entries = await convert_history_to_rank_entries(player_data)
    
    # Filter by days if needed
    if days and rank_entries:
        threshold_date = (datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
        rank_entries = [entry for entry in rank_entries if entry['timestamp'] >= threshold_date]
    
    return rank_entries

async def generate_rank_graph(rank_history: list, player_name: str) -> BytesIO:
    """Generate a beautiful rank graph using matplotlib and return it as a BytesIO object."""
    if not rank_history:
        # Create a simple error image
        width, height = 800, 400
        img = Image.new('RGB', (width, height), color=BACKGROUND_COLOR.replace('#', ''))
        draw = ImageDraw.Draw(img)
        
        # Try to load a font, fall back to default if not available
        try:
            font = ImageFont.truetype("Arial.ttf", 24)
        except IOError:
            font = ImageFont.load_default()
            
        text = f"No rank history data available for {player_name}"
        text_width = draw.textlength(text, font=font)
        draw.text(((width - text_width) / 2, height / 2), text, fill=TEXT_COLOR.replace('#', ''), font=font)
        
        buffer = BytesIO()
        img.save(buffer, "PNG")
        buffer.seek(0)
        return buffer

    # Set the Matplotlib style for a dark theme
    plt.style.use('dark_background')
    
    # Create a DataFrame for easier manipulation
    df = pd.DataFrame(rank_history)
    
    # Convert timestamps to datetime
    df['datetime'] = pd.to_datetime(df['timestamp'])
    
    # Set up the figure with dark mode styling
    fig, ax = plt.subplots(figsize=(12, 6), dpi=100, facecolor=BACKGROUND_COLOR)
    ax.set_facecolor(BACKGROUND_COLOR)
    
    # Plot the rankScore as a line - NOTE THAT WE'RE KEEPING THE # IN THE COLOR CODES
    ax.plot(df['datetime'], df['rankScore'], 
            marker='o', 
            markersize=5,
            linewidth=2.5,
            color=PRIMARY_COLOR,  # Keep the # prefix
            linestyle='-',
            alpha=0.9)
    
    # Fill area below the line
    ax.fill_between(df['datetime'], df['rankScore'], 
                   color=PRIMARY_COLOR,  # Keep the # prefix
                   alpha=0.2)
    
    # Setup grid
    ax.grid(True, linestyle='--', alpha=0.3, color=GRID_COLOR)  # Keep the # prefix
    
    # Format X-axis with dates - improved formatting
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d'))  # Month Day format
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))  # Better tick spacing
    plt.xticks(rotation=45)
    plt.gcf().autofmt_xdate()  # Auto-format the dates
    
    # Ensuring Y-axis starts at 0 or the minimum value minus some padding
    y_min = max(0, min(df['rankScore']) - 100)
    y_max = max(df['rankScore']) + 100
    ax.set_ylim([y_min, y_max])
    
    # Add league bands if we have leagueNumber in the data
    if 'leagueNumber' in df.columns and not df['leagueNumber'].isna().all():
        # Get unique leagues in the data
        unique_leagues = df['leagueNumber'].unique()
        
        # Find the approximate rankScore boundaries for each league
        league_boundaries = {}
        for league_num in sorted(unique_leagues):
            league_data = df[df['leagueNumber'] == league_num]
            league_name = LEAGUE_MAPPING.get(league_num, f"League {league_num}")
            league_boundaries[league_name] = (league_data['rankScore'].min(), league_data['rankScore'].max())
        
        # Draw subtle bands for each league
        y_min, y_max = ax.get_ylim()
        for league_name, (score_min, score_max) in league_boundaries.items():
            color = LEAGUE_COLORS.get(league_name, "#FFFFFF")  # Keep the # prefix
            ax.axhspan(score_min, score_max, alpha=0.1, color=color, label=league_name)
            # Add a label for each league
            ax.text(df['datetime'].iloc[0], (score_min + score_max) / 2, 
                   f" {league_name}", verticalalignment='center',
                   color=color, fontsize=9, alpha=0.7)
    
    # Add titles and labels with styling
    ax.set_title(f"Rank History for {player_name}", 
                color=TEXT_COLOR,  # Keep the # prefix
                fontsize=16, 
                fontweight='bold', 
                pad=20)
    
    ax.set_xlabel("Date", color=TEXT_COLOR, fontsize=12, labelpad=10)  # Keep the # prefix
    ax.set_ylabel("Rank Score", color=TEXT_COLOR, fontsize=12, labelpad=10)  # Keep the # prefix
    
    # Style the tick labels
    ax.tick_params(axis='both', colors=TEXT_COLOR)  # Keep the # prefix
    
    # Add current rank information
    latest_data = df.iloc[-1]
    current_rank = latest_data['rankScore']
    current_league = LEAGUE_MAPPING.get(latest_data.get('leagueNumber', 0), "Unknown")
    
    # Calculate change from first data point
    first_rank = df.iloc[0]['rankScore']
    rank_change = current_rank - first_rank
    change_text = f"↑ +{rank_change}" if rank_change >= 0 else f"↓ {rank_change}"
    change_color = SECONDARY_COLOR if rank_change >= 0 else ACCENT_COLOR  # Keep the # prefix
    
    # Add stats box
    stats_text = (
        f"Current Score: {current_rank}\n"
        f"League: {current_league}\n"
        f"Change: {change_text}"
    )
    props = dict(boxstyle='round,pad=0.5', facecolor=BACKGROUND_COLOR, 
                alpha=0.7, edgecolor=GRID_COLOR)  # Keep the # prefix
    ax.text(0.02, 0.98, stats_text, transform=ax.transAxes, fontsize=10,
            verticalalignment='top', bbox=props, color=TEXT_COLOR)  # Keep the # prefix
    
    # Add markers for min and max points
    max_idx = df['rankScore'].idxmax()
    min_idx = df['rankScore'].idxmin()
    
    ax.plot(df.loc[max_idx, 'datetime'], df.loc[max_idx, 'rankScore'], 
            'o', ms=8, color=SECONDARY_COLOR,  # Keep the # prefix
            alpha=0.8, label='Highest')
    
    ax.plot(df.loc[min_idx, 'datetime'], df.loc[min_idx, 'rankScore'], 
            'o', ms=8, color=ACCENT_COLOR,  # Keep the # prefix
            alpha=0.8, label='Lowest')
    
    # Add a legend
    ax.legend(loc='lower right', facecolor=BACKGROUND_COLOR, 
             edgecolor=GRID_COLOR,  # Keep the # prefix
             labelcolor=TEXT_COLOR)  # Keep the # prefix
    
    # Add a subtle watermark
    fig.text(0.5, 0.02, "THE FINALS Discord Bot", 
            ha='center', color=TEXT_COLOR,  # Keep the # prefix
            alpha=0.3, fontsize=8)
    
    # Adjust the layout
    plt.tight_layout()
    
    # Save the figure to a BytesIO object
    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=100, facecolor=BACKGROUND_COLOR)
    plt.close(fig)  # Close the figure to free memory
    
    buffer.seek(0)
    return buffer

async def setup(bot: commands.Bot) -> None:
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__
    
    @bot.tree.command(
        name="rank", 
        description="Display your THE FINALS rank history as a graph"
    )
    async def rank_command(interaction: discord.Interaction):
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        # Defer the response since this might take a moment
        await interaction.response.defer(thinking=True)
        
        # Get the user's linked THE FINALS account from Cosmos DB
        user_link = await get_user_link(interaction.user.id)
        
        if not user_link:
            await interaction.followup.send(
                "You don't have a linked THE FINALS account. Please use the verification system to link your account first.",
                ephemeral=True
            )
            return
            
        in_game_name = user_link.get('in_game_name')
        
        # Get the player's rank history from the API
        rank_history = await get_player_rank_history(in_game_name, DEFAULT_DAYS)
        
        if not rank_history:
            await interaction.followup.send(
                f"No rank history data found for **{in_game_name}**.",
                ephemeral=True
            )
            return
            
        # Calculate the actual number of days in the data
        if len(rank_history) > 1:
            first_date = datetime.datetime.fromisoformat(rank_history[0]['timestamp'])
            last_date = datetime.datetime.fromisoformat(rank_history[-1]['timestamp'])
            days_span = (last_date - first_date).total_seconds() / (24 * 3600)  # More accurate day calculation
            days_span = max(1, int(days_span) + 1)  # Ensure at least 1 day
            days_text = f"the past {days_span} day{'s' if days_span != 1 else ''}"
        else:
            days_text = "today"
            
        # Generate the graph
        buffer = await generate_rank_graph(rank_history, in_game_name)
        
        # Create a file from the buffer
        file = discord.File(fp=buffer, filename="rank_history.png")
        
        # Get the most recent data
        recent_data = rank_history[-1] if rank_history else None
        
        # Create an embed with the graph as an image
        embed = discord.Embed(
            title=f"THE FINALS Rank History for {in_game_name}",
            description=f"Your rank progression over {days_text}",
            color=int(PRIMARY_COLOR.replace('#', '0x'), 16)  # Properly convert color to integer
        )
        
        # Add rank information if available
        if recent_data:
            league = recent_data.get('league') or LEAGUE_MAPPING.get(recent_data.get('leagueNumber', 0), "Unknown")
            embed.add_field(
                name="Current Rank", 
                value=f"{recent_data.get('rankScore', 'Unknown')} points", 
                inline=True
            )
            embed.add_field(
                name="League", 
                value=league, 
                inline=True
            )
            
            # Calculate change from the first recorded data point
            if len(rank_history) > 1:
                first_record = rank_history[0]
                first_score = first_record.get('rankScore', 0)
                current_score = recent_data.get('rankScore', 0)
                change = current_score - first_score
                change_text = f"+{change}" if change >= 0 else f"{change}"
                embed.add_field(
                    name="Change", 
                    value=f"{change_text} points", 
                    inline=True
                )
        
        # Add the timestamp
        embed.timestamp = datetime.datetime.utcnow()
        
        # Set the footer
        embed.set_footer(text=f"Data collected by THE FINALS Discord Bot | Requested by {interaction.user.name}")
        
        # Set the image to the generated graph
        embed.set_image(url="attachment://rank_history.png")
        
        # Send the embed with the attached file
        await interaction.followup.send(embed=embed, file=file)
    
    @bot.tree.command(
        name="rank_compare", 
        description="Compare your THE FINALS rank with another player"
    )
    @app_commands.describe(
        user="The Discord user to compare with (optional)",
        in_game_name="The in-game name to compare with (optional)"
    )
    async def rank_compare_command(
        interaction: discord.Interaction, 
        user: discord.User = None, 
        in_game_name: str = None
    ):
        # Check if feature is enabled for this guild
        if interaction.guild and not bot.is_feature_enabled(feature_name, interaction.guild.id):
            await interaction.response.send_message(
                f"This command is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        # Ensure that either a user or an in-game name is provided
        if not user and not in_game_name:
            await interaction.response.send_message(
                "Please provide either a Discord user or an in-game name to compare with.",
                ephemeral=True
            )
            return
            
        # Defer response
        await interaction.response.defer(thinking=True)
        
        # Get the requesting user's linked account from Cosmos DB
        user_link = await get_user_link(interaction.user.id)
        
        if not user_link:
            await interaction.followup.send(
                "You don't have a linked THE FINALS account. Please use the verification system to link your account first.",
                ephemeral=True
            )
            return
            
        # Get the comparison user's linked account if a Discord user was provided
        compare_in_game_name = in_game_name
        if user and not compare_in_game_name:
            compare_link = await get_user_link(user.id)
            if not compare_link:
                await interaction.followup.send(
                    f"The user {user.mention} doesn't have a linked THE FINALS account.",
                    ephemeral=True
                )
                return
            compare_in_game_name = compare_link.get('in_game_name')
        
        # Get rank histories from the API
        user_name = user_link.get('in_game_name')
        user_history = await get_player_rank_history(user_name)
        compare_history = await get_player_rank_history(compare_in_game_name)
        
        if not user_history:
            await interaction.followup.send(
                f"No rank history data found for your account ({user_name}).",
                ephemeral=True
            )
            return
            
        if not compare_history:
            await interaction.followup.send(
                f"No rank history data found for {compare_in_game_name}.",
                ephemeral=True
            )
            return
        
        # Generate a comparison graph
        # Set the Matplotlib style for a dark theme
        plt.style.use('dark_background')
        
        # Create DataFrames
        df_user = pd.DataFrame(user_history)
        df_compare = pd.DataFrame(compare_history)
        
        # Convert timestamps to datetime
        df_user['datetime'] = pd.to_datetime(df_user['timestamp'])
        df_compare['datetime'] = pd.to_datetime(df_compare['timestamp'])
        
        # Set up the figure
        fig, ax = plt.subplots(figsize=(12, 6), dpi=100, facecolor=BACKGROUND_COLOR)
        ax.set_facecolor(BACKGROUND_COLOR)
        
        # Plot both lines - keep the # in the color codes
        ax.plot(df_user['datetime'], df_user['rankScore'], 
                marker='o', markersize=4, linewidth=2.5, 
                color=PRIMARY_COLOR,
                linestyle='-', alpha=0.8, label=user_name)
                
        ax.plot(df_compare['datetime'], df_compare['rankScore'], 
                marker='s', markersize=4, linewidth=2.5, 
                color=SECONDARY_COLOR,
                linestyle='-', alpha=0.8, label=compare_in_game_name)
        
        # Setup grid
        ax.grid(True, linestyle='--', alpha=0.3, color=GRID_COLOR)
        
        # Format X-axis
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
        plt.xticks(rotation=45)
        
        # Set y-axis range
        all_scores = list(df_user['rankScore']) + list(df_compare['rankScore'])
        y_min = max(0, min(all_scores) - 100)
        y_max = max(all_scores) + 100
        ax.set_ylim([y_min, y_max])
        
        # Add titles and labels
        ax.set_title(f"Rank Comparison: {user_name} vs {compare_in_game_name}", 
                    color=TEXT_COLOR, 
                    fontsize=14, fontweight='bold')
        
        ax.set_xlabel("Date", color=TEXT_COLOR, fontsize=12)
        ax.set_ylabel("Rank Score", color=TEXT_COLOR, fontsize=12)
        
        # Style tick labels
        ax.tick_params(axis='both', colors=TEXT_COLOR)
        
        # Add a legend
        ax.legend(loc='upper left', facecolor=BACKGROUND_COLOR, 
                edgecolor=GRID_COLOR, 
                labelcolor=TEXT_COLOR)
        
        # Add a subtle watermark
        fig.text(0.5, 0.02, "THE FINALS Discord Bot", 
                ha='center', color=TEXT_COLOR, 
                alpha=0.3, fontsize=8)
        
        # Adjust layout
        plt.tight_layout()
        
        # Save the figure
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=100, facecolor=BACKGROUND_COLOR)
        plt.close(fig)
        buffer.seek(0)
        
        # Create file and embed
        file = discord.File(fp=buffer, filename="rank_comparison.png")
        
        # Get current data for both players
        user_current = user_history[-1] if user_history else None
        compare_current = compare_history[-1] if compare_history else None
        
        # Calculate the actual time range in the data
        all_dates = list(df_user['datetime']) + list(df_compare['datetime'])
        if all_dates:
            first_date = min(all_dates)
            last_date = max(all_dates)
            days_span = (last_date - first_date).days + 1
            days_text = f"the past {days_span} day{'s' if days_span != 1 else ''}"
        else:
            days_text = "recent data"
        
        # Create the embed
        embed = discord.Embed(
            title=f"THE FINALS Rank Comparison",
            description=f"Comparing rank progression over {days_text}",
            color=int(PRIMARY_COLOR.replace('#', '0x'), 16)  # Properly convert color to integer
        )
        
        # Add player data
        if user_current and compare_current:
            user_score = user_current.get('rankScore', 0)
            compare_score = compare_current.get('rankScore', 0)
            
            user_league = user_current.get('league') or LEAGUE_MAPPING.get(user_current.get('leagueNumber', 0), "Unknown")
            compare_league = compare_current.get('league') or LEAGUE_MAPPING.get(compare_current.get('leagueNumber', 0), "Unknown")
            
            embed.add_field(
                name=f"{user_name}",
                value=f"Rank: {user_score}\nLeague: {user_league}",
                inline=True
            )
            
            embed.add_field(
                name=f"{compare_in_game_name}",
                value=f"Rank: {compare_score}\nLeague: {compare_league}",
                inline=True
            )
            
            # Calculate the difference
            diff = user_score - compare_score
            diff_text = f"+{diff}" if diff >= 0 else f"{diff}"
            
            embed.add_field(
                name="Difference",
                value=f"{user_name} is {diff_text} points {'ahead' if diff >= 0 else 'behind'}",
                inline=False
            )
        
        # Set the image to the generated graph
        embed.set_image(url="attachment://rank_comparison.png")
        
        # Add the timestamp
        embed.timestamp = datetime.datetime.utcnow()
        
        # Set the footer
        embed.set_footer(text=f"Data collected by THE FINALS Discord Bot | Requested by {interaction.user.name}")
        
        # Send the embed with the attached file
        await interaction.followup.send(embed=embed, file=file)
        
    # Register the commands
    print(f"Registered rank commands")
