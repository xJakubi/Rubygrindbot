import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import asyncio
from typing import Dict, Any, List, Optional
from azure.cosmos import CosmosClient
from datetime import datetime
import os
import io
import matplotlib.pyplot as plt
import time

# Command Module Configuration
DISPLAY_NAME = "Leaderboard"
DESCRIPTION = "Shows the server leaderboard for THE FINALS"
ENABLED_BY_DEFAULT = False

# THE FINALS API Configuration
API_ENDPOINT = "https://api.the-finals-leaderboard.com/v1/leaderboard"
LEADERBOARD_VERSION = "s5"
PLATFORM = "crossplay"

# Design Configuration
BACKGROUND_COLOR = '#1a1a1a'  # Dark background
PRIMARY_COLOR = '#4e9eff'    # Blue for bars
TEXT_COLOR = '#ffffff'       # White text
GRID_COLOR = '#333333'      # Dark gray grid

# API Rate Limiting Configuration
MAX_REQUESTS_PER_MINUTE = 1000  # Maximum requests per minute
REQUEST_COOLDOWN = 1000 / MAX_REQUESTS_PER_MINUTE  # Time between requests in seconds

class LeaderboardCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cosmos_endpoint = os.getenv("COSMOS_ENDPOINT")
        self.cosmos_key = os.getenv("COSMOS_KEY")
        self.cosmos_database = os.getenv("COSMOS_DATABASE", "thefinalsdb")
        self.last_request_time = 0  # Time of last API request
        self.request_semaphore = asyncio.Semaphore(5)  # Limit concurrent requests
        
    async def get_cosmos_client(self):
        return CosmosClient(self.cosmos_endpoint, self.cosmos_key)

    async def get_linked_users(self) -> List[Dict[str, Any]]:
        """Get all linked users from the user_links container"""
        try:
            client = await self.get_cosmos_client()
            database = client.get_database_client(self.cosmos_database)
            container = database.get_container_client("user_links")
            
            query = "SELECT * FROM c"
            items = list(await asyncio.to_thread(lambda: list(container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))))
            
            print(f"[Leaderboard] Found {len(items)} user links")
            return items
        except Exception as e:
            print(f"[Leaderboard] Error getting linked users: {str(e)}")
            return []

    async def respect_rate_limit(self):
        """Ensure we don't exceed API rate limits"""
        current_time = time.time()
        elapsed = current_time - self.last_request_time
        
        if elapsed < REQUEST_COOLDOWN:
            await asyncio.sleep(REQUEST_COOLDOWN - elapsed)
        
        self.last_request_time = time.time()

    async def get_player_rank_data(self, player_name: str) -> Optional[Dict[str, Any]]:
        """Get player rank data directly from THE FINALS API"""
        try:
            # Use semaphore to limit concurrent requests
            async with self.request_semaphore:
                # Respect rate limiting
                await self.respect_rate_limit()
                
                print(f"[Leaderboard] Fetching rank data for: {player_name}")
                # First try to search with the full name
                search_name = player_name
                
                url = f"{API_ENDPOINT}/{LEADERBOARD_VERSION}/{PLATFORM}"
                
                async with aiohttp.ClientSession() as session:
                    params = {'name': search_name}
                    async with session.get(url, params=params) as response:
                        print(f"[Leaderboard] API Response status: {response.status}")
                        if response.status == 200:
                            data = await response.json()
                            if 'data' in data and data['data']:
                                # First try exact match (case insensitive)
                                for player in data['data']:
                                    if player.get('name', '').lower() == player_name.lower():
                                        print(f"[Leaderboard] Found exact match for {player_name}")
                                        return player
                                
                                # If no exact match and player_name contains #, try without the # part
                                if '#' in player_name:
                                    clean_name = player_name.split('#')[0]
                                    for player in data['data']:
                                        if player.get('name', '').lower() == clean_name.lower():
                                            print(f"[Leaderboard] Found match for {player_name} as {clean_name}")
                                            return player
                                
                                # If still no match but we got results, try partial match
                                if '#' in player_name:
                                    clean_name = player_name.split('#')[0]
                                    # Try again with just the clean name if first attempt with full name failed
                                    params = {'name': clean_name}
                                    async with session.get(url, params=params) as clean_response:
                                        if clean_response.status == 200:
                                            clean_data = await clean_response.json()
                                            if 'data' in clean_data and clean_data['data']:
                                                # Now try to find matches with the clean name
                                                for player in clean_data['data']:
                                                    if player.get('name', '').lower() == clean_name.lower():
                                                        print(f"[Leaderboard] Found match for {player_name} using clean name: {clean_name}")
                                                        return player
                                
                                # Last resort: return first result if it seems relevant
                                if data['data']:
                                    player = data['data'][0]
                                    search_name_lower = search_name.lower()
                                    if search_name_lower in player.get('name', '').lower():
                                        print(f"[Leaderboard] Found partial match: {player.get('name')} for search {search_name}")
                                        return player
                        elif response.status == 429:  # Too Many Requests
                            print(f"[Leaderboard] Rate limited! Waiting before retry...")
                            await asyncio.sleep(10)  # Wait 10 seconds before retrying
                            return await self.get_player_rank_data(player_name)
                        else:
                            print(f"[Leaderboard] API request failed: {await response.text()}")
            return None
        except Exception as e:
            print(f"[Leaderboard] Error getting rank data for {player_name}: {str(e)}")
            return None

    async def get_all_linked_users_rank_data(self, linked_users: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Get rank data for all linked users directly"""
        print(f"[Leaderboard] Getting rank data for {len(linked_users)} linked users")
        
        # Results list
        leaderboard_data = []
        
        # Process users in batches to avoid overwhelming the API
        batch_size = 10
        
        # Only process users with in_game_name
        valid_users = [user for user in linked_users if user.get('in_game_name')]
        print(f"[Leaderboard] Found {len(valid_users)} users with valid in-game names")
        
        for i in range(0, len(valid_users), batch_size):
            batch = valid_users[i:i+batch_size]
            print(f"[Leaderboard] Processing batch {i//batch_size + 1}/{(len(valid_users)-1)//batch_size + 1}")
            
            # Create tasks to fetch player data in parallel
            tasks = []
            for user in batch:
                player_name = user.get('in_game_name')
                task = asyncio.create_task(self.get_player_rank_data(player_name))
                tasks.append((user, task))
            
            # Wait for all tasks to complete
            for user, task in tasks:
                try:
                    player_data = await task
                    if player_data and player_data.get('rankScore') is not None:
                        leaderboard_data.append({
                            'discord_id': user.get('discord_id'),
                            'name': user.get('in_game_name'),
                            'rank_score': player_data.get('rankScore', 0),
                            'league': player_data.get('league', 'Unknown'),
                            'rank': player_data.get('rank', 0),
                            'timestamp': datetime.utcnow().isoformat()
                        })
                        print(f"[Leaderboard] Added {user.get('in_game_name')} with score {player_data.get('rankScore')}")
                    else:
                        print(f"[Leaderboard] No rank data found for {user.get('in_game_name')}")
                except Exception as e:
                    print(f"[Leaderboard] Error processing {user.get('in_game_name')}: {e}")
            
            # Add small delay between batches to be nice to the API
            if i + batch_size < len(valid_users):
                await asyncio.sleep(1)
        
        # Sort by rank score (highest first)
        leaderboard_data = sorted(
            leaderboard_data,
            key=lambda x: x.get('rank_score', 0) or 0,
            reverse=True
        )
        
        print(f"[Leaderboard] Found rank data for {len(leaderboard_data)} users")
        
        return leaderboard_data

    async def generate_leaderboard_image(self, users: List[Dict[str, Any]]) -> io.BytesIO:
        """Generate a graphical leaderboard image"""
        # Get top 10 users
        top_users = users[:10]
        
        # If no users, create a placeholder image
        if not top_users:
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(10, 5), dpi=100, facecolor=BACKGROUND_COLOR)
            ax.text(0.5, 0.5, "No leaderboard data available", 
                    ha='center', va='center', fontsize=18, color=TEXT_COLOR)
            ax.axis('off')
            
            buffer = io.BytesIO()
            plt.savefig(buffer, format='png', facecolor=BACKGROUND_COLOR)
            plt.close(fig)
            buffer.seek(0)
            return buffer
        
        # Set up Matplotlib for a dark theme
        plt.style.use('dark_background')
        
        # Create figure and axis
        fig, ax = plt.subplots(figsize=(12, 6), dpi=100, facecolor=BACKGROUND_COLOR)
        ax.set_facecolor(BACKGROUND_COLOR)
        
        # Prepare data
        names = [user.get("name", "Unknown")[:15] for user in top_users]  # Truncate long names
        scores = [user.get("rank_score", 0) or 0 for user in top_users]
        
        # Create horizontal bar chart
        bars = ax.barh(names, scores, height=0.6, color=PRIMARY_COLOR, alpha=0.8)
        
        # Add value labels
        for bar in bars:
            width = bar.get_width()
            label_x_pos = width + 50
            ax.text(label_x_pos, bar.get_y() + bar.get_height()/2, f'{int(width)}', 
                    color=TEXT_COLOR, va='center')
        
        # Add title and labels
        ax.set_title('THE FINALS Leaderboard - Top 10 Players', fontsize=16, color=TEXT_COLOR)
        ax.set_xlabel('Rank Score', fontsize=12, color=TEXT_COLOR)
        
        # Remove y-axis label
        ax.set_ylabel('')
        
        # Style the axes
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['bottom'].set_color(GRID_COLOR)
        ax.spines['left'].set_color(GRID_COLOR)
        
        # Set tick colors
        ax.tick_params(axis='x', colors=TEXT_COLOR)
        ax.tick_params(axis='y', colors=TEXT_COLOR)
        
        # Add grid
        ax.grid(axis='x', linestyle='--', alpha=0.3, color=GRID_COLOR)
        
        # Adjust layout
        plt.tight_layout()
        
        # Save to buffer
        buffer = io.BytesIO()
        plt.savefig(buffer, format='png', facecolor=BACKGROUND_COLOR, bbox_inches='tight')
        plt.close(fig)
        buffer.seek(0)
        
        return buffer

    @app_commands.command(
        name="leaderboard",
        description="Shows the leaderboard of top THE FINALS players"
    )
    async def leaderboard_command(self, interaction: discord.Interaction):
        """Shows the leaderboard of top THE FINALS players"""
        # Defer response as this might take a moment
        await interaction.response.defer(thinking=True)
        
        # Get all linked users
        linked_users = await self.get_linked_users()
        
        if not linked_users:
            await interaction.followup.send(
                "No linked accounts found. Users need to link their THE FINALS accounts first.",
                ephemeral=True
            )
            return
            

        
        start_time = time.time()
        # Get rank data for all linked users
        leaderboard_data = await self.get_all_linked_users_rank_data(linked_users)
        fetch_time = time.time() - start_time
        
        if not leaderboard_data:
            await interaction.followup.send(
                "No rank data found for any linked accounts. Please try again later.",
                ephemeral=True
            )
            return
        
        # Generate leaderboard image
        image_buffer = await self.generate_leaderboard_image(leaderboard_data)
        image_file = discord.File(fp=image_buffer, filename="leaderboard.png")
        
        # Create embed
        embed = discord.Embed(
            title="THE FINALS Leaderboard",
            description=f"Top {min(len(leaderboard_data), 10)} players ranked by score",
            color=int(PRIMARY_COLOR.replace('#', '0x'), 16)
        )
        
        # Add player listings
        player_text = ""
        for idx, user in enumerate(leaderboard_data[:10]):
            medal = "🥇" if idx == 0 else "🥈" if idx == 1 else "🥉" if idx == 2 else f"{idx+1}."
            try:
                discord_user = await self.bot.fetch_user(int(user['discord_id']))
                discord_name = discord_user.mention if discord_user else f"<@{user['discord_id']}>"
            except Exception:
                discord_name = f"<@{user['discord_id']}>"
            
            league_info = ""
            if user.get("league") and user.get("league") != "Unknown":
                league_info = f" | {user.get('league')}"
                
            player_text += f"{medal} **{user['name']}** - {int(user['rank_score'])}{league_info} ({discord_name})\n"
        
        if player_text:
            embed.add_field(name="Rankings", value=player_text, inline=False)
        else:
            embed.add_field(name="Rankings", value="No ranked players found", inline=False)
        
        # Set the image
        embed.set_image(url="attachment://leaderboard.png")
        
        # Add timestamp and footer
        embed.timestamp = datetime.utcnow()
        embed.set_footer(text=f"THE FINALS Discord Bot | Fetch time: {fetch_time:.1f}s")
        
        # Send the leaderboard
        await interaction.followup.send(embed=embed, file=image_file)

async def setup(bot: commands.Bot):
    await bot.add_cog(LeaderboardCommand(bot))