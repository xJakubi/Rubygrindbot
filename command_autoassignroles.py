import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
import datetime
import traceback
import io
import base64
from typing import Optional, List, Dict, Any
import aiohttp
import json
import time
import re

# Handle optional Azure imports
try:
    from azure.cosmos import CosmosClient, PartitionKey
except ImportError:
    print("[WARNING] Azure Cosmos DB SDK not installed. Database features will be disabled.")
    # Create stub classes
    class CosmosClient:
        def __init__(self, *args, **kwargs):
            pass
    class PartitionKey:
        def __init__(self, *args, **kwargs):
            pass

# Optional Azure OpenAI imports
try:
    from azure.ai.inference import ChatCompletionsClient # type: ignore
    from azure.ai.inference.models import SystemMessage, UserMessage # type: ignore
    from azure.identity import DefaultAzureCredential # type: ignore
    AZURE_AI_AVAILABLE = True
except ImportError:
    print("[WARNING] Azure OpenAI SDK not installed. Image analysis will use fallback mode.")
    AZURE_AI_AVAILABLE = False

DISPLAY_NAME = "Auto Assign Roles"
DESCRIPTION = "Creates a role selection embed with dropdown menu for users to self-assign roles"
ENABLED_BY_DEFAULT = False  # Off by default since it requires special permissions

# Define role groups
SELF_ASSIGNABLE_ROLES = ["Light", "Medium", "Heavy", "NA", "EU"]
VERIFICATION_ROLES = ["Verify: K/D & Win Rate", "Pro role"]

# Define roles that will be created if they don't exist
KD_ROLES = ["KD 1+", "KD 1.5+", "KD 2.0+", "KD 2.5+"]
WINRATE_ROLES = ["Win rate 50%+", "Win rate 55%+", "Win rate 60%+", "Win rate 70%+"]
SPECIAL_ROLES = ["Pro"]

# For storing the message ID of the embed
MESSAGE_KEY = "role_selection_message"
CONTAINER_NAME = "bot_config"

class RoleSelectionView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)  # No timeout - persistent view
        self.cog = cog
        
        # Add dropdown menu
        self.add_item(RoleDropdown(cog))

class RoleDropdown(discord.ui.Select):
    def __init__(self, cog):
        self.cog = cog
        
        # Create options from role lists
        options = []
        
        # Add self-assignable roles
        for role_name in SELF_ASSIGNABLE_ROLES:
            options.append(
                discord.SelectOption(
                    label=role_name,
                    description=f"Select to get the {role_name} role",
                    emoji="✅" if role_name in ["Light", "Medium", "Heavy"] else "🌎" if role_name in ["NA", "EU"] else None
                )
            )
        
        # Add verification roles
        for role_name in VERIFICATION_ROLES:
            options.append(
                discord.SelectOption(
                    label=role_name,
                    description=f"Requires verification of stats",
                    emoji="🔍"
                )
            )
            
        super().__init__(
            placeholder="Select a role...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="role_selection"
        )
        
    async def callback(self, interaction: discord.Interaction):
        # Get the selected role name
        selected_role = self.values[0]
        
        # Handle based on role type
        if selected_role in SELF_ASSIGNABLE_ROLES:
            await self.handle_self_assignable(interaction, selected_role)
        elif selected_role in VERIFICATION_ROLES:
            await self.handle_verification_role(interaction, selected_role)
        else:
            await interaction.response.send_message("Invalid role selection.", ephemeral=True)
    
    async def handle_self_assignable(self, interaction: discord.Interaction, role_name: str):
        """Handle self-assignable roles that don't require verification"""
        try:
            # Try to find the role
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            
            # Create role if it doesn't exist
            if not role:
                # Set role color based on role type
                if role_name == "Light":
                    color = discord.Color.from_rgb(95, 255, 91)  # Light green
                elif role_name == "Medium":
                    color = discord.Color.from_rgb(255, 128, 0)  # Orange
                elif role_name == "Heavy":
                    color = discord.Color.from_rgb(255, 45, 45)  # Red
                elif role_name in ["NA", "EU"]:
                    color = discord.Color.from_rgb(0, 128, 255)  # Blue
                else:
                    color = discord.Color.default()
                
                role = await interaction.guild.create_role(name=role_name, color=color)
            
            # Clear other roles in the same category if needed
            if role_name in ["Light", "Medium", "Heavy"]:
                # Remove other class roles
                for class_role in ["Light", "Medium", "Heavy"]:
                    if class_role != role_name:
                        other_role = discord.utils.get(interaction.guild.roles, name=class_role)
                        if other_role and other_role in interaction.user.roles:
                            await interaction.user.remove_roles(other_role)
            
            if role_name in ["NA", "EU"]:
                # Remove other region roles
                for region_role in ["NA", "EU"]:
                    if region_role != role_name:
                        other_role = discord.utils.get(interaction.guild.roles, name=region_role)
                        if other_role and other_role in interaction.user.roles:
                            await interaction.user.remove_roles(other_role)
            
            # Assign the role
            if role not in interaction.user.roles:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"You have been given the **{role_name}** role!", ephemeral=True)
            else:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message(f"The **{role_name}** role has been removed.", ephemeral=True)
                
        except Exception as e:
            await interaction.response.send_message(f"Error assigning role: {str(e)}", ephemeral=True)
            print(f"[AutoRoles] Error assigning role: {str(e)}")
            traceback.print_exc()
    
    async def handle_verification_role(self, interaction: discord.Interaction, role_name: str):
        """Handle roles that require verification"""
        try:
            # Send DM to the user
            await interaction.response.send_message(
                "I've sent you a DM with instructions for verification. Please check your direct messages.",
                ephemeral=True
            )
            
            # Create the verification DM embed
            verification_embed = discord.Embed(
                title=f"Role Verification: {role_name}",
                description="To verify for this role, please follow these instructions:",
                color=discord.Color.blue()
            )
            
            verification_embed.add_field(
                name="Instructions",
                value=(
                    "1. Open THE FINALS game and go to your Career page\n"
                    "2. Take a screenshot of the ENTIRE page (do not crop)\n"
                    "3. Make sure all stats are clearly visible including:\n"
                    "   • Eliminations\n"
                    "   • Deaths\n"
                    "   • Wins\n"
                    "   • Losses\n"
                    "4. Send the screenshot as a reply to this message\n\n"
                    "**IMPORTANT**: Using alt accounts or manipulated screenshots will result in a ban."
                ),
                inline=False
            )
            
            if role_name == "Pro role":
                verification_embed.add_field(
                    name="Pro Role Requirements",
                    value="• Win rate of 60%+\n• K/D ratio of 1.6+\n• Must have Ruby rank",
                    inline=False
                )
            else:
                verification_embed.add_field(
                    name="Verification Process",
                    value=(
                        "Your stats will be analyzed to assign the appropriate role based on your K/D ratio "
                        "and Win rate percentage."
                    ),
                    inline=False
                )
                
            verification_embed.set_footer(text="Reply with your screenshot to continue the verification process")
            
            # Send the DM
            try:
                dm_channel = await interaction.user.create_dm()
                verification_message = await dm_channel.send(embed=verification_embed)
                
                # Store the verification request in the cog
                self.cog.verification_requests[verification_message.id] = {
                    "user_id": interaction.user.id,
                    "guild_id": interaction.guild.id,
                    "requested_role": role_name,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }
                
            except discord.Forbidden:
                await interaction.followup.send(
                    "I couldn't send you a DM. Please enable direct messages from server members and try again.",
                    ephemeral=True
                )
            
        except Exception as e:
            await interaction.followup.send(f"Error processing verification: {str(e)}", ephemeral=True)
            print(f"[AutoRoles] Error processing verification: {str(e)}")
            traceback.print_exc()

class AutoAssignRolesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.verification_requests = {}  # Store active verification requests
        self.config_key = "auto_assign_roles"
        self.message_ids = {}  # guild_id -> message_id
        
        # Azure OpenAI configuration - use environment variables
        self.api_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.model_name = os.getenv("AZURE_OPENAI_MODEL", "gpt-4o")
        
        # Azure Cosmos DB configuration
        self.cosmos_endpoint = os.getenv("COSMOS_ENDPOINT")
        self.cosmos_key = os.getenv("COSMOS_KEY")
        self.cosmos_database = os.getenv("COSMOS_DATABASE")
        
        # Initialize Cosmos DB client
        try:
            self.cosmos_client = CosmosClient(self.cosmos_endpoint, credential=self.cosmos_key)
            self.database = self.cosmos_client.get_database_client(self.cosmos_database)
            
            # Create or get container for bot configuration
            try:
                self.config_container = self.database.create_container_if_not_exists(
                    id=CONTAINER_NAME,
                    partition_key=PartitionKey(path="/guild_id")
                )
            except Exception as e:
                print(f"[AutoRoles] Error creating container: {str(e)}")
                self.config_container = self.database.get_container_client(CONTAINER_NAME)
            
            print(f"[AutoRoles] Connected to Azure Cosmos DB: {self.cosmos_database}/{CONTAINER_NAME}")
        except Exception as e:
            print(f"[AutoRoles] Error connecting to Azure Cosmos DB: {str(e)}")
            traceback.print_exc()
            self.cosmos_client = None
            self.database = None
            self.config_container = None
        
        # Start background task for initialization
        self.bot.loop.create_task(self.startup_check())
        
    async def startup_check(self):
        """Initial check when bot starts up"""
        # Wait until bot is ready
        await self.bot.wait_until_ready()
        print("[AutoRoles] Bot is ready, checking for enabled feature in guilds...")
        
        try:
            # Wait a bit more to ensure all systems are loaded
            await asyncio.sleep(5)
            
            # Register the persistent view
            self.bot.add_view(RoleSelectionView(self))
            
            # Check all guilds
            for guild in self.bot.guilds:
                try:
                    # Check if feature is enabled
                    if self.bot.is_feature_enabled("auto_assign_roles", guild.id):
                        print(f"[AutoRoles] Feature enabled for {guild.name}, checking roles")
                        
                        # Ensure required roles exist
                        await self.ensure_roles_exist(guild)
                        
                        # Check if we have a saved message ID
                        message_id = await self.load_message_id(guild.id)
                        if message_id:
                            # Try to delete old message if it exists
                            try:
                                for channel in guild.text_channels:
                                    try:
                                        message = await channel.fetch_message(int(message_id))
                                        if message:
                                            await message.delete()
                                            print(f"[AutoRoles] Deleted old message in {channel.name}")
                                            break
                                    except:
                                        continue
                            except Exception as e:
                                print(f"[AutoRoles] Error deleting old message: {str(e)}")
                        
                        # Get the configured channel
                        channel_id = await self.load_channel_id(guild.id)
                        if channel_id:
                            channel = guild.get_channel(int(channel_id))
                            if channel:
                                # Create new embed in the configured channel
                                await self.create_role_embed(channel)
                                continue
                        
                        print(f"[AutoRoles] No channel configured for {guild.name}")
                except Exception as e:
                    print(f"[AutoRoles] Error checking {guild.name}: {str(e)}")
                    traceback.print_exc()
        except Exception as e:
            print(f"[AutoRoles] Startup error: {str(e)}")
            traceback.print_exc()
    
    async def get_cosmos_config_item(self, guild_id, key):
        """Get configuration item from Cosmos DB"""
        if not self.config_container:
            return None
            
        try:
            # Build the item ID
            item_id = f"{guild_id}_{key}"
            
            # Query for the item
            query = f"SELECT * FROM c WHERE c.id = '{item_id}'"
            items = list(self.config_container.query_items(
                query=query,
                enable_cross_partition_query=True
            ))
            
            if items:
                return items[0].get("value")
            return None
        except Exception as e:
            print(f"[AutoRoles] Error getting config from Cosmos DB: {str(e)}")
            return None
    
    async def set_cosmos_config_item(self, guild_id, key, value):
        """Set configuration item in Cosmos DB"""
        if not self.config_container:
            return False
            
        try:
            # Build the item ID and data
            item_id = f"{guild_id}_{key}"
            item = {
                "id": item_id,
                "guild_id": str(guild_id),
                "key": key,
                "value": value,
                "updated_at": datetime.datetime.utcnow().isoformat()
            }
            
            # Upsert the item
            self.config_container.upsert_item(item)
            return True
        except Exception as e:
            print(f"[AutoRoles] Error setting config in Cosmos DB: {str(e)}")
            return False
    
    async def load_message_id(self, guild_id):
        """Load the embed message ID from Cosmos DB"""
        return await self.get_cosmos_config_item(guild_id, f"{self.config_key}_message")
    
    async def save_message_id(self, guild_id, message_id):
        """Save the embed message ID to Cosmos DB"""
        success = await self.set_cosmos_config_item(guild_id, f"{self.config_key}_message", str(message_id) if message_id else None)
        if success:
            # Update local cache
            self.message_ids[guild_id] = message_id
            print(f"[AutoRoles] Saved message ID {message_id} for guild {guild_id}")
        return success
    
    async def load_channel_id(self, guild_id):
        """Load the configured channel ID from Cosmos DB"""
        return await self.get_cosmos_config_item(guild_id, f"{self.config_key}_channel")
            
    async def save_channel_id(self, guild_id, channel_id):
        """Save the configured channel ID to Cosmos DB"""
        success = await self.set_cosmos_config_item(guild_id, f"{self.config_key}_channel", str(channel_id) if channel_id else None)
        if success:
            print(f"[AutoRoles] Saved channel ID {channel_id} for guild {guild_id}")
        return success
    
    async def ensure_roles_exist(self, guild):
        """Ensure all required roles exist in the guild"""
        try:
            # Create K/D roles
            for role_name in KD_ROLES:
                if not discord.utils.get(guild.roles, name=role_name):
                    await guild.create_role(
                        name=role_name,
                        color=discord.Color.from_rgb(255, 0, 128)  # Pink
                    )
                    print(f"[AutoRoles] Created role: {role_name}")
            
            # Create Win Rate roles
            for role_name in WINRATE_ROLES:
                if not discord.utils.get(guild.roles, name=role_name):
                    await guild.create_role(
                        name=role_name,
                        color=discord.Color.from_rgb(0, 191, 255)  # Sky blue
                    )
                    print(f"[AutoRoles] Created role: {role_name}")
            
            # Create Special roles
            for role_name in SPECIAL_ROLES:
                if not discord.utils.get(guild.roles, name=role_name):
                    await guild.create_role(
                        name=role_name,
                        color=discord.Color.from_rgb(255, 215, 0)  # Gold
                    )
                    print(f"[AutoRoles] Created role: {role_name}")
                    
            # Create self-assignable roles if they don't exist
            for role_name in SELF_ASSIGNABLE_ROLES:
                if not discord.utils.get(guild.roles, name=role_name):
                    color = discord.Color.default()
                    
                    if role_name == "Light":
                        color = discord.Color.from_rgb(95, 255, 91)  # Light green
                    elif role_name == "Medium":
                        color = discord.Color.from_rgb(255, 128, 0)  # Orange
                    elif role_name == "Heavy":
                        color = discord.Color.from_rgb(255, 45, 45)  # Red
                    elif role_name in ["NA", "EU"]:
                        color = discord.Color.from_rgb(0, 128, 255)  # Blue
                    
                    await guild.create_role(
                        name=role_name,
                        color=color
                    )
                    print(f"[AutoRoles] Created role: {role_name}")
            
            print(f"[AutoRoles] All required roles created for {guild.name}")
            return True
        except Exception as e:
            print(f"[AutoRoles] Error creating roles: {str(e)}")
            traceback.print_exc()
            return False
    
    async def create_role_embed(self, channel):
        """Create the role selection embed"""
        try:
            embed = discord.Embed(
                title="THE FINALS Role Selection",
                description=(
                    "Select a role from the dropdown menu below to get started!\n\n"
                    "**Class Roles**\n"
                    "• Light - Fast-moving class with lower health\n"
                    "• Medium - Balanced class with moderate health and speed\n"
                    "• Heavy - High health but slower movement\n\n"
                    "**Region Roles**\n"
                    "• NA - North America\n"
                    "• EU - Europe\n\n"
                    "**Verification Required**\n"
                    "• Verify: K/D & Win Rate - Get roles based on your stats\n"
                    "• Pro role - For verified high-tier players (Requires Ruby rank, 60%+ win rate, 1.6+ K/D)"
                ),
                color=discord.Color.blue()
            )
            
            embed.set_footer(text="Select a role from the dropdown menu below")
            embed.timestamp = datetime.datetime.utcnow()
            
            # Send the embed with the view
            message = await channel.send(embed=embed, view=RoleSelectionView(self))
            
            # Save the message ID
            await self.save_message_id(channel.guild.id, message.id)
            await self.save_channel_id(channel.guild.id, channel.id)
            
            print(f"[AutoRoles] Created role embed in {channel.name} ({message.id})")
            return message
        except Exception as e:
            print(f"[AutoRoles] Error creating role embed: {str(e)}")
            traceback.print_exc()
            return None
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for DM responses with screenshots or manual stats input"""
        # Ignore messages from bots or in servers
        if message.author.bot or not isinstance(message.channel, discord.DMChannel):
            return
            
        # Check if this is a response to a verification request
        for req_id, req_data in list(self.verification_requests.items()):
            if req_data["user_id"] == message.author.id:
                # Check if the message has an attachment (screenshot)
                if message.attachments:
                    for attachment in message.attachments:
                        # Check if it's an image file
                        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg']):
                            await message.channel.send("Analyzing your screenshot... This may take a moment.")
                            
                            # Process the screenshot
                            await self.process_verification_screenshot(
                                message, 
                                attachment, 
                                req_data
                            )
                            # Remove from active requests
                            self.verification_requests.pop(req_id, None)
                            return
                    
                    # No valid images found
                    await message.channel.send(
                        "I couldn't find a valid image. Please send a PNG or JPG screenshot of your entire Career stats page."
                    )
                # Check if this is a manual stats input
                elif "elims:" in message.content.lower() or "eliminations:" in message.content.lower():
                    await message.channel.send("Processing your manual stats input...")
                    
                    # Process the manual stats
                    await self.process_manual_stats(message, req_data)
                    # Remove from active requests
                    self.verification_requests.pop(req_id, None)
                    return
                else:
                    await message.channel.send(
                        "Please send a screenshot of your Career stats page or enter your stats manually using the format provided."
                    )
    
    async def process_manual_stats(self, message, req_data):
        """Process manually entered stats"""
        try:
            content = message.content.lower()
            
            # Extract stats with regex
            elims_match = re.search(r"elims?:?\s*(\d+)", content) or re.search(r"eliminations:?\s*(\d+)", content)
            deaths_match = re.search(r"deaths:?\s*(\d+)", content)
            wins_match = re.search(r"wins:?\s*(\d+)", content)
            losses_match = re.search(r"losses:?\s*(\d+)", content)
            ruby_match = re.search(r"ruby:?\s*(yes|no|true|false|y|n)", content)
            
            # Check if we have enough data
            if not (elims_match and deaths_match):
                await message.channel.send(
                    "I couldn't find elimination and death stats in your message. Please try again with the format:\n"
                    "```\n"
                    "Elims: [number]\n"
                    "Deaths: [number]\n"
                    "Wins: [number]\n"
                    "Losses: [number]\n"
                    "Ruby: yes/no\n"
                    "```"
                )
                return
                
            # Get the guild and user
            guild = self.bot.get_guild(req_data["guild_id"])
            if not guild:
                await message.channel.send("Error: I can't find the server. Please try again later.")
                return
                    
            user = guild.get_member(req_data["user_id"])
            if not user:
                await message.channel.send("Error: I can't find your user in the server. Please rejoin and try again.")
                return
            
            # Parse the stats
            elims = int(elims_match.group(1))
            deaths = int(deaths_match.group(1))
            
            # Calculate K/D ratio
            kd_ratio = elims / deaths if deaths > 0 else elims
            
            # Parse wins and losses if available
            win_rate = 0
            if wins_match and losses_match:
                wins = int(wins_match.group(1))
                losses = int(losses_match.group(1))
                total_matches = wins + losses
                win_rate = (wins / total_matches) * 100 if total_matches > 0 else 0
            
            # Check Ruby rank
            has_ruby_rank = False
            if ruby_match:
                ruby_value = ruby_match.group(1).lower()
                has_ruby_rank = ruby_value in ["yes", "true", "y"]
            
            # Create analysis result
            analysis_result = {
                "kd_ratio": kd_ratio,
                "win_rate": win_rate,
                "has_ruby_rank": has_ruby_rank,
                "manual_input": True,
                "raw_stats": {
                    "eliminations": elims,
                    "deaths": deaths,
                    "wins": wins_match.group(1) if wins_match else "N/A",
                    "losses": losses_match.group(1) if losses_match else "N/A"
                }
            }
            
            # Debug logging
            print("\n=== MANUAL STATS ANALYSIS ===")
            print(f"Eliminations: {elims}")
            print(f"Deaths: {deaths}")
            print(f"K/D Ratio: {kd_ratio:.2f}")
            if wins_match and losses_match:
                print(f"Wins: {wins}")
                print(f"Losses: {losses}")
                print(f"Win Rate: {win_rate:.2f}%")
            print(f"Ruby Rank: {has_ruby_rank}")
            print("=============================\n")
            
            # Process the analysis and assign roles
            assigned_roles = await self.assign_roles_from_analysis(user, guild, analysis_result, req_data["requested_role"])

            # Log the verification WITHOUT screenshot (correctly)
            try:
                server_logs_cog = self.bot.get_cog("ServerLogsCog")
                if server_logs_cog and self.bot.is_feature_enabled("server_logs", guild.id):
                    await server_logs_cog.handle_verification_log(
                        user,
                        req_data["requested_role"],
                        image_url=None  # No image for manual stats
                    )
                    print(f"[AutoRoles] Manual verification logged to server logs")
            except Exception as e:
                print(f"[AutoRoles] Error logging verification to server logs: {str(e)}")
                traceback.print_exc()
            # Save verification result to Cosmos DB
            try:
                if self.config_container:
                    # Create verification record
                    verification_record = {
                        "id": f"verification_{user.id}_{datetime.datetime.utcnow().timestamp()}",
                        "guild_id": str(guild.id),
                        "user_id": str(user.id),
                        "username": user.name,
                        "requested_role": req_data["requested_role"],
                        "kd_ratio": analysis_result.get("kd_ratio", 0),
                        "win_rate": analysis_result.get("win_rate", 0),
                        "has_ruby_rank": analysis_result.get("has_ruby_rank", False),
                        "assigned_roles": assigned_roles,
                        "verification_date": datetime.datetime.utcnow().isoformat(),
                        "verification_type": "manual"
                    }
                    
                    self.config_container.upsert_item(verification_record)
                    print(f"[AutoRoles] Saved manual verification record for {user.name}")
            except Exception as e:
                print(f"[AutoRoles] Error saving verification record: {str(e)}")
            
            # Send confirmation message
            if assigned_roles:
                role_list = ", ".join([f"**{role}**" for role in assigned_roles])
                await message.channel.send(
                    f"Verification complete! You've been assigned the following role(s): {role_list}\n\n"
                    f"Your stats:\n"
                    f"• K/D Ratio: {kd_ratio:.2f}\n"
                    f"• Win Rate: {win_rate:.2f}%\n"
                    f"• Ruby Role in server: {'Yes' if has_ruby_rank else 'No'}"
                )
            else:
                await message.channel.send(
                    f"Verification complete, but you don't meet the requirements for any special roles.\n\n"
                    f"Your stats:\n"
                    f"• K/D Ratio: {kd_ratio:.2f}\n"
                    f"• Win Rate: {win_rate:.2f}%\n"
                    f"• Ruby Rank: {'Yes' if has_ruby_rank else 'No'}"
                )
                try:
                    # Find the ServerLogsCog
                    server_logs_cog = self.bot.get_cog("ServerLogsCog")
                    if server_logs_cog and self.bot.is_feature_enabled("server_logs", guild.id):
                        # Create an embed for the verification
                        embed = discord.Embed(
                            title="Role Verification Submitted",
                            description=f"User **{user.name}** submitted verification for **{req_data['requested_role']}**",
                            color=0x1abc9c,  # Teal
                            timestamp=datetime.datetime.utcnow()
                        )
                        
                        embed.add_field(name="Assigned Roles", value=role_list if 'role_list' in locals() else "None", inline=False)
                        embed.add_field(name="K/D Ratio", value=f"{analysis_result.get('kd_ratio', 0):.2f}", inline=True)
                        embed.add_field(name="Win Rate", value=f"{analysis_result.get('win_rate', 0):.2f}%", inline=True)
                        
                        # Check for Ruby role in user roles
                        has_ruby_role = self.check_ruby_role(user)
                        embed.add_field(name="Ruby Rank", value=f"{'Yes' if analysis_result.get('has_ruby_rank', False) or has_ruby_role else 'No'}", inline=True)
                        
                        if user.avatar:
                            embed.set_author(name=user.name, icon_url=user.avatar.url)
                        
                        # Log to the server logs channel without attachment URL
                        await server_logs_cog.handle_verification_log(user, req_data['requested_role'], image_url=None)
                        print(f"[AutoRoles] Verification logged to server logs")
                except Exception as e:
                    print(f"[AutoRoles] Error logging verification to server logs: {str(e)}")
                    traceback.print_exc()

        except Exception as e:
            await message.channel.send(f"Error processing verification: {str(e)}")
            print(f"[AutoRoles] Error processing verification: {str(e)}")
            traceback.print_exc()
    
    async def process_verification_screenshot(self, message, attachment, req_data):
        """Process a verification screenshot"""
        try:
            # Download the image
            image_data = await attachment.read()
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            
            # Get the guild and user
            guild_id = req_data["guild_id"]
            guild = self.bot.get_guild(guild_id)
            if not guild:
                await message.channel.send("Error: I can't find the server. Please try again later.")
                return
                
            user_id = req_data["user_id"]
            user = guild.get_member(user_id)
            if not user:
                await message.channel.send("Error: I can't find your user in the server. Please rejoin and try again.")
                return
        
            # Debug info
            print(f"\n=== VERIFICATION REQUEST ===")
            print(f"Guild: {guild.name} (ID: {guild.id})")
            print(f"User: {user.name} (ID: {user.id})")
            print(f"Requested role: {req_data['requested_role']}")
            print(f"Bot permissions: {guild.get_member(self.bot.user.id).guild_permissions}")
            print(f"=========================\n")
            
            # Direct image parsing approach instead of using Azure OpenAI
            analysis_result = await self.extract_stats_from_image(image_data, message)

            if not analysis_result:
                await message.channel.send(
                    "I couldn't analyze your screenshot properly. Please try again with a clearer screenshot of your stats page. Make sure your entire career stats page is visible."
                )
                return
                
            # Process the analysis and assign roles
            assigned_roles = await self.assign_roles_from_analysis(user, guild, analysis_result, req_data["requested_role"])

            # Log the verification WITH screenshot
            try:
                server_logs_cog = self.bot.get_cog("ServerLogsCog")
                if server_logs_cog and self.bot.is_feature_enabled("server_logs", guild.id):
                    await server_logs_cog.handle_verification_log(
                        user,
                        req_data["requested_role"],
                        image_url=attachment.url  # Include the screenshot URL
                    )
                    print(f"[AutoRoles] Screenshot verification logged to server logs")
            except Exception as e:
                print(f"[AutoRoles] Error logging verification to server logs: {str(e)}")
                traceback.print_exc()
            
            # Save verification result to Cosmos DB
            try:
                if self.config_container:
                    # Create verification record
                    verification_record = {
                        "id": f"verification_{user.id}_{datetime.datetime.utcnow().timestamp()}",
                        "guild_id": str(guild.id),
                        "user_id": str(user.id),
                        "username": user.name,
                        "requested_role": req_data["requested_role"],
                        "kd_ratio": analysis_result.get("kd_ratio", 0),
                        "win_rate": analysis_result.get("win_rate", 0),
                        "has_ruby_rank": analysis_result.get("has_ruby_rank", False),
                        "assigned_roles": assigned_roles,
                        "verification_date": datetime.datetime.utcnow().isoformat(),
                        "verification_type": "screenshot"
                    }
                    
                    self.config_container.upsert_item(verification_record)
                    print(f"[AutoRoles] Saved verification record for {user.name}")
            except Exception as e:
                print(f"[AutoRoles] Error saving verification record: {str(e)}")
                traceback.print_exc()
            
            # Check if user has Ruby role in the server
            has_ruby_role = self.check_ruby_role(user)
            
            # Send confirmation message
            if assigned_roles:
                role_list = ", ".join([f"**{role}**" for role in assigned_roles])
                await message.channel.send(
                    f"Verification complete! You've been assigned the following role(s): {role_list}\n\n"
                    f"Your stats:\n"
                    f"• K/D Ratio: {analysis_result.get('kd_ratio', 0):.2f}\n"
                    f"• Win Rate: {analysis_result.get('win_rate', 0):.2f}%\n"
                    f"• Ruby Rank: {'Yes' if analysis_result.get('has_ruby_rank', False) or has_ruby_role else 'No'}"
                )
            else:
                await message.channel.send(
                    f"Verification complete, but you don't meet the requirements for any special roles.\n\n"
                    f"Your stats:\n"
                    f"• K/D Ratio: {analysis_result.get('kd_ratio', 0):.2f}\n"
                    f"• Win Rate: {analysis_result.get('win_rate', 0):.2f}%\n"
                    f"• Ruby Rank: {'Yes' if analysis_result.get('has_ruby_rank', False) or has_ruby_role else 'No'}"
                )
        
        except Exception as e:
            await message.channel.send(f"Error processing verification: {str(e)}")
            print(f"[AutoRoles] Error processing verification: {str(e)}")
            traceback.print_exc()
    
    async def extract_stats_from_image(self, image_data, message):
        """
        Extract stats directly from the image without using Azure OpenAI
        This is a new implementation that uses OCR techniques (simulated here)
        """
        try:
            # Try to use Azure OpenAI if configured, else attempt to use pytesseract for OCR
            analysis_result = {}
            
            # First try Azure OpenAI if available
            if self.api_key and self.api_endpoint:
                print("[AutoRoles] Attempting Azure OpenAI analysis")
                image_base64 = base64.b64encode(image_data).decode('utf-8')
                ai_analysis = await self.analyze_stats_with_azure(image_base64)
                if ai_analysis:
                    return ai_analysis
            
            # If we get here, we couldn't use Azure or it failed, so we'll ask the user for manual input
            await message.channel.send(
                "I'm having trouble analyzing your screenshot. Please make sure your entire career stats page is clearly visible and try again."
            )
            
            # Return None to indicate we couldn't automatically extract stats
            # The user will need to use manual entry
            return None
            
        except Exception as e:
            print(f"[AutoRoles] Error extracting stats from image: {str(e)}")
            traceback.print_exc()
            return None
    
    async def analyze_stats_with_azure(self, image_base64):
        """Improved Azure OpenAI analysis function"""
        try:
            if not self.api_key or not self.api_endpoint:
                print("[AutoRoles] Azure OpenAI API not configured")
                return None
                
            # Prepare the API request
            headers = {
                "Content-Type": "application/json",
                "api-key": self.api_key
            }
            
            # Create the prompt with clearer instructions for the model
            system_message = (
                "You are an expert at analyzing THE FINALS game statistics from screenshots. "
                "Extract the following information in a structured format:\n"
                "1. K/D Ratio = Eliminations / Deaths (or calculate it if you can see both numbers)\n"
                "2. Win Rate = (Wins / (Wins + Losses)) * 100 (or extract it directly if visible)\n"
                "3. Check if the player has Ruby rank\n\n"
                "Be precise in your analysis. Only extract stats that are clearly visible. "
                "IMPORTANT: For Win Rate, make sure to calculate it as a percentage between 0-100, NOT as a decimal."
                "Your response should be in this exact format:\n"
                "Eliminations: [number]\n"
                "Deaths: [number]\n"
                "KD Ratio: [number]\n"
                "Wins: [number]\n"
                "Losses: [number]\n"
                "Win Rate: [number]%\n"
                "Ruby Rank: Yes/No"
            )
            
            user_message = (
                "Here's a screenshot of my THE FINALS career stats page. Please extract my stats."
            )
            
            # Prepare the payload
            payload = {
                "messages": [
                    {"role": "system", "content": system_message},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_message},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                            }
                        ]
                    }
                ],
                "max_tokens": 500,
                "temperature": 0.3
            }
            
            # Use aiohttp for async HTTP request
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_endpoint, json=payload, headers=headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"[AutoRoles] API Error: {response.status} - {error_text}")
                        return None
                        
                    result = await response.json()
                    
            # Extract the analysis from the response
            if "choices" in result and len(result["choices"]) > 0:
                analysis_text = result["choices"][0]["message"]["content"]
                
                # Log the full analysis
                print(f"\n=== IMAGE ANALYSIS RESULT ===")
                print(analysis_text)
                print(f"===========================\n")
                
                # Parse the analysis with improved regex patterns
                result = {"analysis": analysis_text}
                
                # Extract values using regex
                elims_match = re.search(r"Eliminations:?\s*([\d,]+)", analysis_text, re.IGNORECASE)
                deaths_match = re.search(r"Deaths:?\s*([\d,]+)", analysis_text, re.IGNORECASE)
                kd_match = re.search(r"KD Ratio:?\s*(\d+\.?\d*)", analysis_text, re.IGNORECASE)
                wins_match = re.search(r"Wins:?\s*([\d,]+)", analysis_text, re.IGNORECASE)
                losses_match = re.search(r"Losses:?\s*([\d,]+)", analysis_text, re.IGNORECASE)
                wr_match = re.search(r"Win Rate:?\s*(\d+\.?\d*)%?", analysis_text, re.IGNORECASE)
                ruby_match = re.search(r"Ruby Rank:?\s*(Yes|No|True|False)", analysis_text, re.IGNORECASE)
                
                # Calculate K/D ratio from raw stats if available
                if elims_match and deaths_match:
                    elims = int(elims_match.group(1).replace(',', ''))
                    deaths = int(deaths_match.group(1).replace(',', ''))
                    result["kd_ratio"] = elims / deaths if deaths > 0 else elims
                    print(f"✅ K/D calculated: {result['kd_ratio']}")
                elif kd_match:
                    # Use directly provided K/D ratio
                    result["kd_ratio"] = float(kd_match.group(1))
                    print(f"✅ K/D extracted: {result['kd_ratio']}")
                else:
                    result["kd_ratio"] = 0
                    print("❌ Could not determine K/D ratio")
                
                # Calculate win rate from raw stats if available
                if wins_match and losses_match:
                    try:
                        wins_str = wins_match.group(1)
                        losses_str = losses_match.group(1)
                        
                        print(f"DEBUG - Raw wins text: '{wins_str}'")
                        print(f"DEBUG - Raw losses text: '{losses_str}'")
                        
                        # Remove commas before converting to integers
                        wins = int(wins_str.replace(',', ''))
                        losses = int(losses_str.replace(',', ''))
                        
                        print(f"DEBUG - Parsed wins: {wins}")
                        print(f"DEBUG - Parsed losses: {losses}")
                        
                        total_matches = wins + losses
                        print(f"DEBUG - Total matches: {total_matches}")
                        
                        if total_matches > 0:
                            # Explicitly calculate win rate step by step
                            fraction = wins / total_matches
                            print(f"DEBUG - Win fraction: {fraction}")
                            
                            percentage = fraction * 100
                            print(f"DEBUG - Win percentage: {percentage}%")
                            
                            # Ensure we're setting the correct value
                            result["win_rate"] = percentage
                            print(f"✅ Win rate calculated from raw data: {result['win_rate']}%")
                        else:
                            result["win_rate"] = 0
                            print("❌ Could not calculate win rate: no matches played")
                    except ValueError as e:
                        print(f"❌ Error parsing win/loss numbers: {e}")
                elif wr_match:
                    # Direct extraction from win rate line
                    try:
                        win_rate = float(wr_match.group(1))
                        result["win_rate"] = win_rate
                        print(f"✅ Win rate directly extracted: {win_rate}%")
                    except ValueError:
                        result["win_rate"] = 0
                        print("❌ Error parsing win rate text")
                else:
                    result["win_rate"] = 0
                    print("❌ Could not determine win rate")
                
                # Determine Ruby rank status
                if ruby_match:
                    result["has_ruby_rank"] = ruby_match.group(1).lower() in ["yes", "true"]
                    print(f"✅ Ruby rank: {result['has_ruby_rank']}")
                else:
                    # Look for Ruby rank mention elsewhere in the text
                    result["has_ruby_rank"] = "ruby" in analysis_text.lower() and not ("not ruby" in analysis_text.lower() or "no ruby" in analysis_text.lower())
                    print(f"⚠️ Ruby rank inferred: {result['has_ruby_rank']}")
                
                return result
            else:
                print(f"[AutoRoles] Invalid API response format: {result}")
                return None
                
        except Exception as e:
            print(f"[AutoRoles] Error analyzing with Azure: {str(e)}")
            traceback.print_exc()
            return None
    
    def check_ruby_role(self, user):
        """Check if user has a role named 'Ruby' in the server"""
        return any(role.name == "Ruby" for role in user.roles)

    async def assign_roles_from_analysis(self, user, guild, analysis_result, requested_role):
        """Assign roles based on the analysis results"""
        try:
            # Debug info
            print("\n=== ROLE ASSIGNMENT START ===")
            print(f"Guild: {guild.name} (ID: {guild.id})")
            print(f"User: {user.name} (ID: {user.id})")
            print(f"Requested role: {requested_role}")

            print(f"K/D ratio: {analysis_result.get('kd_ratio', 0)}")
            print(f"Win rate: {analysis_result.get('win_rate', 0)}%")
            
            # IMPORTANT: ONLY use the server role check for Ruby verification
            has_ruby_role = self.check_ruby_role(user)
            print(f"Has Ruby role in server: {has_ruby_role}")
            
            # Check bot permissions
            bot_member = guild.get_member(self.bot.user.id)
            if not bot_member:
                print("❌ Bot not found in guild")
                return []
                
            print(f"Bot permissions: {bot_member.guild_permissions}")
            if not bot_member.guild_permissions.manage_roles:
                print("❌ Bot doesn't have 'Manage Roles' permission")
                return []
                
            assigned_roles = []
            
            kd_ratio = analysis_result.get("kd_ratio", 0)
            win_rate = analysis_result.get("win_rate", 0)
            
            # FIXED: For Pro role verification, ONLY consider server roles
            # For display purposes in messages, we can use the analysis result
            has_ruby_rank_display = analysis_result.get("has_ruby_rank", False) or has_ruby_role
            
            if requested_role == "Verify: K/D & Win Rate":
                # Assign K/D roles
                kd_role_to_assign = None
                
                print("\nK/D Role Calculation:")
                if kd_ratio >= 2.5:
                    kd_role_to_assign = "KD 2.5+"
                    print(f"K/D {kd_ratio} ≥ 2.5 ✓")
                elif kd_ratio >= 2.0:
                    kd_role_to_assign = "KD 2.0+"
                    print(f"K/D {kd_ratio} ≥ 2.0 ✓")
                elif kd_ratio >= 1.5:
                    kd_role_to_assign = "KD 1.5+"
                    print(f"K/D {kd_ratio} ≥ 1.5 ✓")
                elif kd_ratio >= 1.0:
                    kd_role_to_assign = "KD 1+"
                    print(f"K/D {kd_ratio} ≥ 1.0 ✓")
                else:
                    print(f"K/D {kd_ratio} < 1.0 ✗")
                
                # Remove redundant check that always prints the same message
                # print(f"K/D {kd_ratio} < 1.0 ✗")
                
                print(f"K/D role selected: {kd_role_to_assign}")
                
                if kd_role_to_assign:
                    role = discord.utils.get(guild.roles, name=kd_role_to_assign)
                    if role:
                        print(f"Found role in guild: {role.name} (ID: {role.id})")
                        # Check role hierarchy
                        if bot_member.top_role > role:
                            try:
                                # Remove any other KD roles first
                                for role_name in KD_ROLES:
                                    other_role = discord.utils.get(guild.roles, name=role_name)
                                    if other_role and other_role in user.roles:
                                        await user.remove_roles(other_role)
                                        print(f"Removed role: {role_name}")
                                
                                # Add the new role
                                await user.add_roles(role)
                                assigned_roles.append(kd_role_to_assign)
                                print(f"✅ Successfully assigned role: {kd_role_to_assign}")
                            except discord.Forbidden:
                                print(f"❌ Permission error assigning {kd_role_to_assign}")
                        else:
                            print(f"❌ Role hierarchy error: Bot's role is below {kd_role_to_assign}")
                    else:
                        print(f"❌ Role not found: {kd_role_to_assign}")
                
                # Assign Win Rate roles with similar detailed logging
                wr_role_to_assign = None
                
                print("\nWin Rate Role Calculation:")
                if win_rate >= 70:
                    wr_role_to_assign = "Win rate 70%+"
                    print(f"Win Rate {win_rate}% ≥ 70% ✓")
                elif win_rate >= 60:
                    wr_role_to_assign = "Win rate 60%+"
                    print(f"Win Rate {win_rate}% ≥ 60% ✓")
                elif win_rate >= 55:
                    wr_role_to_assign = "Win rate 55%+"
                    print(f"Win Rate {win_rate}% ≥ 55% ✓")
                elif win_rate >= 50:
                    wr_role_to_assign = "Win rate 50%+"
                    print(f"Win Rate {win_rate}% ≥ 50% ✓")
                else:
                    print(f"Win Rate {win_rate}% < 50% ✗")
                    
                print(f"Win Rate role selected: {wr_role_to_assign}")
                
                if wr_role_to_assign:
                    role = discord.utils.get(guild.roles, name=wr_role_to_assign)
                    if role:
                        print(f"Found role in guild: {role.name} (ID: {role.id})")
                        # Check role hierarchy
                        if bot_member.top_role > role:
                            try:
                                # Remove any other Win Rate roles first
                                for role_name in WINRATE_ROLES:
                                    other_role = discord.utils.get(guild.roles, name=role_name)
                                    if other_role and other_role in user.roles:
                                        await user.remove_roles(other_role)
                                        print(f"Removed role: {role_name}")
                                
                                # Add the new role
                                await user.add_roles(role)
                                assigned_roles.append(wr_role_to_assign)
                                print(f"✅ Successfully assigned role: {wr_role_to_assign}")
                            except discord.Forbidden:
                                print(f"❌ Permission error assigning {wr_role_to_assign}")
                        else:
                            print(f"❌ Role hierarchy error: Bot's role is below {wr_role_to_assign}")
                    else:
                        print(f"❌ Role not found: {wr_role_to_assign}")
            
            elif requested_role == "Pro role":
                # Check if user meets all requirements for Pro role
                print("\nPro Role Calculation:")
                meets_kd = kd_ratio >= 2
                meets_wr = win_rate >= 60
                
                print(f"K/D Requirement: {kd_ratio} ≥ 2+ = {meets_kd}")
                print(f"Win Rate Requirement: {win_rate}% ≥ 60% = {meets_wr}")
                print(f"Ruby Rank Requirement (must have 'Ruby' role in server): {has_ruby_role}")
                
                # FIXED: Only assign Pro role if they have the Ruby role in the server (not from analysis)
                if meets_kd and meets_wr and has_ruby_role:
                    pro_role = discord.utils.get(guild.roles, name="Pro")
                    if pro_role:
                        try:
                            await user.add_roles(pro_role)
                            assigned_roles.append("Pro")
                            print("✅ Successfully assigned Pro role")
                        except discord.Forbidden:
                            print("❌ Permission error assigning Pro role")
                    else:
                        print("❌ Pro role not found in guild")
                else:
                    print("❌ User doesn't meet Pro role requirements")
            
            # Return the assigned roles
            print(f"\nAssigned roles: {assigned_roles}")
            print("=== ROLE ASSIGNMENT COMPLETE ===\n")
            
            return assigned_roles
                    
        except discord.Forbidden as e:
            print(f"❌ Permission error assigning roles: {str(e)}")
            traceback.print_exc()
            return []
        except Exception as e:
            print(f"❌ Error assigning roles: {str(e)}")
            traceback.print_exc()
            return []
        
    @app_commands.command(
        name="autoassignroles",
        description="Set up the role selection embed in this channel"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def autoassignroles_command(self, interaction: discord.Interaction):
        """Single command to set up the role selection embed in the current channel"""
        # Check if feature is enabled
        if not self.bot.is_feature_enabled("auto_assign_roles", interaction.guild.id):
            await interaction.response.send_message(
                f"This feature is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        # Defer response
        await interaction.response.defer(thinking=True)
        
        # Check if there's an existing message
        old_message_id = await self.load_message_id(interaction.guild.id)
        if old_message_id:
            # Try to delete the old message
            try:
                old_channel_id = await self.load_channel_id(interaction.guild.id)
                if old_channel_id:
                    old_channel = interaction.guild.get_channel(int(old_channel_id))
                    if old_channel:
                        try:
                            old_message = await old_channel.fetch_message(int(old_message_id))
                            if old_message:
                                await old_message.delete()
                                print(f"[AutoRoles] Deleted old message in {old_channel.name}")
                        except:
                            pass
            except Exception as e:
                print(f"[AutoRoles] Error deleting old message: {str(e)}")
        
        # Ensure roles exist
        await self.ensure_roles_exist(interaction.guild)
        
        # Create new embed
        message = await self.create_role_embed(interaction.channel)
        
        if message:
            await interaction.followup.send(
                "✅ Role selection embed has been created in this channel.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Could not create role selection embed. Please make sure the bot has these permissions:\n"
                "- Send Messages\n"
                "- Embed Links\n"
                "- Manage Roles",
                ephemeral=True
            )



async def setup(bot: commands.Bot) -> None:
    """Setup function for the cog"""
    try:
        # Get the feature name from the module name
        feature_name = "auto_assign_roles"
        
        # Register the cog
        await bot.add_cog(AutoAssignRolesCog(bot))
        
        print(f"[AutoRoles] Module registered as '{feature_name}'")
    except Exception as e:
        print(f"[AutoRoles] Error during setup: {str(e)}")
        traceback.print_exc()