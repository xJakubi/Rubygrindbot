import discord
import asyncio
import os
from discord.ext import tasks, commands
from discord import app_commands
from azure.cosmos import CosmosClient, PartitionKey, exceptions
import datetime
import importlib
from typing import Dict, List, Any
import aiohttp
import urllib.parse

# Display name and description for setup menu
DISPLAY_NAME = "Auto Rank Roles"
DESCRIPTION = "Automatically assigns and updates roles based on players' current rank in THE FINALS."
ENABLED_BY_DEFAULT = False

# Define rank tier thresholds
RANK_TIERS = {
    "Bronze": (0, 9999),
    "Silver": (10000, 19999),
    "Gold": (20000, 29999),
    "Platinum": (30000, 39999),
    "Diamond": (40000, float('inf')),
    # Ruby is handled separately as it's top 500
}

# Rank colors for role creation
RANK_COLORS = {
    "Bronze": discord.Color.from_rgb(205, 127, 50),
    "Silver": discord.Color.from_rgb(192, 192, 192),
    "Gold": discord.Color.from_rgb(255, 215, 0),
    "Platinum": discord.Color.from_rgb(229, 228, 226),
    "Diamond": discord.Color.from_rgb(185, 242, 255),
    "Ruby": discord.Color.from_rgb(224, 17, 95)
}

# Use environment variables if available; otherwise, fall back to default values.
COSMOS_ENDPOINT = os.getenv("COSMOS_ENDPOINT") 
COSMOS_KEY = os.getenv("COSMOS_KEY") 
COSMOS_DATABASE = os.getenv("COSMOS_DATABASE") 

# API endpoints
NEW_API_BASE_URL = "https://thefinals.fortunevale.de/api"
CURRENT_SEASON = "s6"  # Update as needed

class RankRolesTask:
    def __init__(self, bot):
        self.bot = bot
        self.cosmos_client = None
        self.database = None
        self.user_links_container = None

    async def initialize_db(self):
        """Initialize Cosmos DB client and containers - only need user_links"""
        try:
            if not self.cosmos_client:
                self.cosmos_client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
                self.database = self.cosmos_client.create_database_if_not_exists(id=COSMOS_DATABASE)
                
                # Get user_links container only
                self.user_links_container = self.database.create_container_if_not_exists(
                    id="user_links",
                    partition_key=PartitionKey(path="/discord_id"),
                    offer_throughput=400
                )
                
                print("[RankRoles] Successfully initialized Cosmos DB connection")
            return True
        except Exception as e:
            print(f"[RankRoles] Error initializing Cosmos DB: {e}")
            return False

    async def get_player_data_from_api(self, player_name: str) -> dict:
        """Fetch player data from the leaderboard API."""
        try:
            encoded_name = urllib.parse.quote(player_name)
            url = f"{NEW_API_BASE_URL}/leaderboard/name/{CURRENT_SEASON}/{encoded_name}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    return None
        except Exception:
            return None

    async def get_latest_rank_data(self, in_game_name: str) -> dict:
        """Get the latest rank data for a player by their in-game name using the API"""
        try:
            # Get player data from API
            player_data = await self.get_player_data_from_api(in_game_name)
            
            if not player_data or "CurrentPlacement" not in player_data:
                return {}
            
            # Extract current data
            current = player_data["CurrentPlacement"]
            
            # Check for score - different field names possible
            score = 0
            if "Score" in current:
                score = current["Score"]
            elif "score" in current:
                score = current["score"]
            
            # Get placement for Ruby check
            placement = current.get("Placement", 0)
            
            return {
                "rankScore": score,
                "rank": placement,
                "isRuby": placement <= 500 and placement > 0
            }
            
        except Exception:
            return {}

    async def get_all_linked_users(self) -> List[Dict[str, Any]]:
        """Get all users that have linked their THE FINALS accounts"""
        try:
            # Query all user links
            query = "SELECT * FROM c"
            
            links = list(self.user_links_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            print(f"[RankRoles] Found {len(links)} linked users")
            return links
        except Exception as e:
            print(f"[RankRoles] Error getting linked users: {e}")
            return []

    async def ensure_rank_roles(self, guild: discord.Guild) -> Dict[str, discord.Role]:
        """Ensure all rank tier roles exist in the guild and return a mapping of tier name to role"""
        rank_roles = {}
        
        # Check if roles exist, create them if they don't
        for tier_name, color in RANK_COLORS.items():
            # Look for existing role
            role = discord.utils.get(guild.roles, name=tier_name)
            
            if not role:
                try:
                    # Create the role if it doesn't exist
                    print(f"[RankRoles] Creating {tier_name} role in {guild.name}")
                    role = await guild.create_role(
                        name=tier_name,
                        color=color,
                        hoist=True,  # Display separately in member list
                        mentionable=True,
                        reason="Automatic rank role creation by THE FINALS Bot"
                    )
                except discord.Forbidden:
                    print(f"[RankRoles] Missing permissions to create {tier_name} role in {guild.name}")
                except Exception as e:
                    print(f"[RankRoles] Error creating {tier_name} role: {e}")
            
            if role:
                rank_roles[tier_name] = role
        
        return rank_roles

    def get_rank_tier(self, rank_score: int, is_top_500: bool = False) -> str:
        """Determine rank tier based on score and Ruby status"""
        # Ruby takes precedence if the player is in top 500
        if is_top_500:
            return "Ruby"
        
        # Otherwise determine by score
        for tier_name, (min_score, max_score) in RANK_TIERS.items():
            if min_score <= rank_score <= max_score:
                return tier_name
                
        # Default to Diamond for very high scores
        return "Diamond"

    async def update_member_roles(self, member: discord.Member, rank_tier: str, rank_roles: Dict[str, discord.Role]):
        """Update a member's rank roles, adding the correct one and removing others"""
        try:
            # Get current rank roles the member has
            current_rank_roles = [role for role in member.roles if role.name in RANK_COLORS]
            
            # Get the appropriate rank role
            new_rank_role = rank_roles.get(rank_tier)
            if not new_rank_role:
                return False
            
            roles_to_remove = [role for role in current_rank_roles if role.id != new_rank_role.id]
            
            # Remove old rank roles
            if roles_to_remove:
                try:
                    await member.remove_roles(*roles_to_remove, reason="Rank tier update")
                except Exception:
                    pass
            
            # Add new rank role if not already assigned
            if new_rank_role not in member.roles:
                try:
                    await member.add_roles(new_rank_role, reason="Rank tier update")
                    return True
                except Exception:
                    return False
            else:
                return True
        except Exception:
            return False

    async def update_rank_roles(self):
        """Updates rank roles for all users in all guilds"""
        print(f"[RankRoles] Starting rank role update at {datetime.datetime.now()}")
        
        # Initialize DB if not already done
        await self.initialize_db()
        
        # Process each guild the bot is in
        for guild in self.bot.guilds:
            # Check if feature is enabled for this guild
            is_enabled = self.bot.is_feature_enabled("rank_roles", guild.id)
            
            if not is_enabled:
                continue
                
            print(f"[RankRoles] Processing guild: {guild.name}")
            
            try:
                # Ensure rank roles exist in this guild
                rank_roles = await self.ensure_rank_roles(guild)
                
                if not rank_roles:
                    print(f"[RankRoles] Failed to create rank roles in {guild.name}, skipping")
                    continue
                
                # Get all linked users
                linked_users = await self.get_all_linked_users()
                
                # Process updates for each linked user
                updated_count = 0
                for link in linked_users:
                    try:
                        discord_id = link.get("discord_id")
                        in_game_name = link.get("in_game_name")
                        
                        if not discord_id or not in_game_name:
                            continue
                            
                        # Get member object from guild
                        member = guild.get_member(int(discord_id))
                        if not member:
                            continue
                        
                        # Get latest rank data from API
                        rank_data = await self.get_latest_rank_data(in_game_name)
                        
                        if not rank_data or 'rankScore' not in rank_data:
                            continue
                        
                        # Get rank score and Ruby status
                        rank_score = rank_data.get('rankScore', 0)
                        is_ruby = rank_data.get('isRuby', False) or rank_data.get('rank', 0) <= 500
                        
                        # Determine rank tier
                        rank_tier = self.get_rank_tier(rank_score, is_ruby)
                        
                        # Update member's roles
                        success = await self.update_member_roles(member, rank_tier, rank_roles)
                        
                        if success:
                            updated_count += 1
                            
                    except Exception:
                        pass
                
                print(f"[RankRoles] Updated {updated_count} members in {guild.name}")
                
            except Exception as e:
                print(f"[RankRoles] Error processing guild {guild.name}: {str(e)}")
        
        print(f"[RankRoles] Completed rank role update at {datetime.datetime.now()}")

    @tasks.loop(minutes=30)  # Changed to 30 minutes
    async def update_rank_roles_task(self):
        """Task that runs every 30 minutes"""
        await self.update_rank_roles()

    @update_rank_roles_task.before_loop
    async def before_update_rank_roles(self):
        """Wait until the bot is ready before starting the task"""
        await self.bot.wait_until_ready()
        print("[RankRoles] Bot is ready, rank roles task will start soon")

    def start_task(self):
        """Start the update rank roles task"""
        self.update_rank_roles_task.start()
        print("[RankRoles] Rank roles update task started (30-minute interval)")

    def stop_task(self):
        """Stop the update rank roles task"""
        self.update_rank_roles_task.cancel()
        print("[RankRoles] Rank roles update task stopped")

class RankRolesCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(
        name="update_ranks_now",
        description="Manually trigger rank role updates"
    )
    @app_commands.default_permissions(administrator=True)
    async def update_ranks(self, interaction: discord.Interaction):
        """Manually trigger rank role updates"""
        await interaction.response.defer(ephemeral=True)
        
        feature_enabled = self.bot.is_feature_enabled("rank_roles", interaction.guild.id)
        
        if not feature_enabled:
            await interaction.followup.send("The rank roles feature is not enabled in this server. Please enable it first using the `/setup` command.", ephemeral=True)
            return
            
        global rank_roles_task
        if rank_roles_task:
            await interaction.followup.send("Starting rank role update...", ephemeral=True)
            await rank_roles_task.update_rank_roles()
            await interaction.followup.send("Rank role update completed! Check the logs for details.", ephemeral=True)
        else:
            await interaction.followup.send("Rank roles task is not initialized.", ephemeral=True)


# The instance of the task
rank_roles_task = None

async def setup(bot):
    """Set up the rank roles task when the module is loaded"""
    global rank_roles_task
    
    # Create and start the task
    rank_roles_task = RankRolesTask(bot)
    
    # First run the task once immediately
    await rank_roles_task.initialize_db()
    
    # Register the command cog - this ensures commands are registered properly
    await bot.add_cog(RankRolesCommands(bot))
    
    # Then start the recurring task
    rank_roles_task.start_task()
    
    # Run update immediately but only after bot is fully ready
    bot.loop.create_task(rank_roles_task.update_rank_roles())
    
    print("[RankRoles] Module initialized with scheduled first run")

def teardown(bot):
    """Clean up when the module is unloaded"""
    global rank_roles_task
    if rank_roles_task:
        rank_roles_task.stop_task()
    
    # Remove the command cog
    bot.remove_cog(RankRolesCommands.__name__)
    
    print("[RankRoles] Module unloaded")