import discord
from discord.ext import commands
from discord import app_commands
import functools
import asyncio
import json
import os
import datetime
from datetime import timedelta
import re
from typing import Dict, Optional, List
from azure.cosmos import CosmosClient, exceptions

DISPLAY_NAME = "/Warning system"
DESCRIPTION = "Command for moderators to warn and timeout users with acknowledgment tracking"
ENABLED_BY_DEFAULT = False
COSMOS_ENDPOINT = "https://thefinalsdb.documents.azure.com:443/"
COSMOS_KEY = os.environ.get("COSMOS_KEY")
COSMOS_DATABASE = "thefinalsdb"
COSMOS_CONTAINER = "warnings"
# File to store warning data
WARNINGS_FILE = "warnings_data.json"

class WarningData:
    def __init__(self):
        self.warnings = {}
        self.load_data()
    
    def load_data(self):
        """Load warnings data from file"""
        try:
            if os.path.exists(WARNINGS_FILE):
                with open(WARNINGS_FILE, 'r') as f:
                    self.warnings = json.load(f)
        except Exception as e:
            print(f"Error loading warnings data: {e}")
            self.warnings = {}
    
    def save_data(self):
        """Save warnings data to file"""
        try:
            with open(WARNINGS_FILE, 'w') as f:
                json.dump(self.warnings, f)
        except Exception as e:
            print(f"Error saving warnings data: {e}")
    
    def add_warning(self, guild_id: int, user_id: int, warning_data: dict):
        """Add a warning for a user"""
        guild_id_str = str(guild_id)
        user_id_str = str(user_id)
        
        if guild_id_str not in self.warnings:
            self.warnings[guild_id_str] = {}
        
        if user_id_str not in self.warnings[guild_id_str]:
            self.warnings[guild_id_str][user_id_str] = []
        
        self.warnings[guild_id_str][user_id_str].append(warning_data)
        self.save_data()
    
    def mark_acknowledged(self, guild_id: int, user_id: int, warning_id: str, acknowledged: bool = True):
        """Mark a warning as acknowledged"""
        guild_id_str = str(guild_id)
        user_id_str = str(user_id)
        
        if guild_id_str in self.warnings and user_id_str in self.warnings[guild_id_str]:
            for warning in self.warnings[guild_id_str][user_id_str]:
                if warning.get("id") == warning_id:
                    warning["acknowledged"] = acknowledged
                    warning["acknowledged_at"] = datetime.datetime.now().isoformat()
                    self.save_data()
                    return True
        return False
    
    def get_unacknowledged_warnings(self, guild_id: int, user_id: int) -> List[dict]:
        """Get all unacknowledged warnings for a user"""
        guild_id_str = str(guild_id)
        user_id_str = str(user_id)
        
        if guild_id_str in self.warnings and user_id_str in self.warnings[guild_id_str]:
            return [w for w in self.warnings[guild_id_str][user_id_str] if not w.get("acknowledged", False)]
        
        return []
    
    def get_warnings(self, guild_id: int, user_id: int) -> List[dict]:
        """Get all warnings for a user"""
        guild_id_str = str(guild_id)
        user_id_str = str(user_id)
        
        if guild_id_str in self.warnings and user_id_str in self.warnings[guild_id_str]:
            return self.warnings[guild_id_str][user_id_str]
        
        return []

# Global warning data instance
warning_data = WarningData()
class CosmosWarningsDB:
    """Class to handle warning data in Cosmos DB"""
    def __init__(self):
        self.initialized = False
        self.initialization_attempted = False
        self.retry_count = 0
        self.max_retries = 3
        
        # Try to initialize immediately
        self.initialize()
    
    def initialize(self):
        """Initialize the CosmosDB connection with retry logic"""
        self.initialization_attempted = True
        
        try:
            print(f"Initializing CosmosDB connection (attempt {self.retry_count + 1}/{self.max_retries})...")
            print(f"Endpoint: {COSMOS_ENDPOINT}")
            # Print key length for debug without exposing the actual key
            key_length = len(COSMOS_KEY) if COSMOS_KEY else 0
            print(f"Key provided: {'Yes' if key_length > 0 else 'No'}, Key length: {key_length} characters")
            
            # Check if we have the basic requirements to connect
            if not COSMOS_ENDPOINT or not COSMOS_KEY:
                print("Error: Missing Cosmos DB endpoint or key.")
                self.initialized = False
                return
            
            # Initialize client
            self.client = CosmosClient(COSMOS_ENDPOINT, credential=COSMOS_KEY)
            
            # Test if can list databases (basic connectivity test)
            print("Testing CosmosDB connection by listing databases...")
            database_list = list(self.client.list_databases())
            print(f"Found {len(database_list)} databases")
            
            # Get or create database
            print(f"Accessing database: {COSMOS_DATABASE}")
            database_exists = any(db['id'] == COSMOS_DATABASE for db in database_list)
            
            if not database_exists:
                print(f"Database {COSMOS_DATABASE} not found, creating it...")
                self.database = self.client.create_database(id=COSMOS_DATABASE)
            else:
                print(f"Using existing database: {COSMOS_DATABASE}")
                self.database = self.client.get_database_client(COSMOS_DATABASE)
            
            # Check if warnings container exists, create if it doesn't
            print("Getting container list...")
            container_list = [c['id'] for c in self.database.list_containers()]
            print(f"Found containers: {', '.join(container_list) if container_list else 'None'}")
            
            if COSMOS_CONTAINER not in container_list:
                print(f"Creating warnings container in CosmosDB")
                self.database.create_container(
                    id=COSMOS_CONTAINER,
                    partition_key={"paths": ["/guild_id"], "kind": "Hash"}
                )
            
            self.container = self.database.get_container_client(COSMOS_CONTAINER)
            
            # Test the connection with a simple query
            print("Testing container with a query...")
            test_query = list(self.container.query_items(
                query="SELECT TOP 1 * FROM c",
                enable_cross_partition_query=True
            ))
            print(f"Query test successful. Found {len(test_query)} items.")
            
            self.initialized = True
            self.retry_count = 0  # Reset retry count on success
            print("✅ CosmosDB warnings database initialized successfully")
            
        except exceptions.CosmosHttpResponseError as e:
            print(f"⚠️ Failed to initialize CosmosDB: {e}")
            print(f"Status code: {e.status_code}, Substatus: {getattr(e, 'sub_status', 'N/A')}")
            print(f"Error details: {getattr(e, 'message', 'No details')}")
            self.initialized = False
            self.retry_if_needed()
        except Exception as e:
            print(f"⚠️ Error initializing CosmosDB: {type(e).__name__}: {str(e)}")
            self.initialized = False
            self.retry_if_needed()
    
    def retry_if_needed(self):
        """Retry initialization if not too many attempts"""
        self.retry_count += 1
        if self.retry_count < self.max_retries:
            print(f"Will retry initialization (attempt {self.retry_count + 1}/{self.max_retries}) in 5 seconds...")
            # We can't use asyncio.sleep here since this is not an async method
            import time
            time.sleep(5)
            self.initialize()
        else:
            print("❌ Max retries reached. CosmosDB initialization failed.")
    
    async def ensure_initialized(self):
        """Ensure DB is initialized before operations"""
        if not self.initialized and not self.initialization_attempted:
            self.initialize()
        return self.initialized
    
    async def save_warning(self, warning_data):
        """Save a warning to Cosmos DB"""
        if not await self.ensure_initialized():
            print(f"CosmosDB not initialized, skipping save for warning {warning_data.get('id', 'unknown')}")
            return False
        
        try:
            # Format the data for Cosmos DB
            cosmos_warning = {
                "id": f"{warning_data['guild_id']}_{warning_data['user_id']}_{warning_data['id']}",
                "guild_id": str(warning_data["guild_id"]),
                "user_id": str(warning_data["user_id"]),
                "warning_id": warning_data["id"],
                "issuer_id": str(warning_data["issuer_id"]),
                "issuer_name": warning_data["issuer_name"],
                "reason": warning_data["reason"],
                "timestamp": warning_data["timestamp"],
                "acknowledged": warning_data.get("acknowledged", False),
                "timeout_applied": warning_data.get("timeout_applied", False),
                "timeout_duration": warning_data.get("timeout_duration", None)
            }
            
            print(f"Saving warning {warning_data['id']} to CosmosDB...")
            
            # Use asyncio to prevent blocking
            await asyncio.to_thread(self.container.upsert_item, body=cosmos_warning)
            print(f"Successfully saved warning {warning_data['id']} to CosmosDB")
            return True
        except exceptions.CosmosHttpResponseError as e:
            print(f"Failed to save warning to CosmosDB: {e}")
            print(f"Status code: {e.status_code}, Substatus: {getattr(e, 'sub_status', 'N/A')}")
            print(f"Error details: {getattr(e, 'message', 'No details')}")
            return False
        except Exception as e:
            print(f"Error saving warning to CosmosDB: {type(e).__name__}: {str(e)}")
            return False
    async def get_user_warnings(self, guild_id, user_id):
        """Get all warnings for a user from CosmosDB"""
        if not await self.ensure_initialized():
            print(f"CosmosDB not initialized, skipping query")
            return []
        
        try:
            guild_id_str = str(guild_id)
            user_id_str = str(user_id)
            
            query = f"SELECT * FROM c WHERE c.guild_id = '{guild_id_str}' AND c.user_id = '{user_id_str}'"
            
            print(f"Querying CosmosDB for warnings - Guild: {guild_id}, User: {user_id}")
            
            # Use asyncio to prevent blocking
            items = await asyncio.to_thread(
                lambda: list(self.container.query_items(
                    query=query,
                    enable_cross_partition_query=True
                ))
            )
            
            print(f"Found {len(items)} warnings in CosmosDB for user {user_id} in guild {guild_id}")
            return items
        
        except exceptions.CosmosHttpResponseError as e:
            print(f"Failed to query warnings from CosmosDB: {e}")
            print(f"Status code: {e.status_code}, Substatus: {getattr(e, 'sub_status', 'N/A')}")
            print(f"Error details: {getattr(e, 'message', 'No details')}")
            return []
        except Exception as e:
            print(f"Error querying warnings from CosmosDB: {type(e).__name__}: {str(e)}")
            return []

# Initialize the CosmosDB handler
cosmos_warnings = CosmosWarningsDB()




#Helper function to check if a feature is enabled in the current guild
def feature_check(bot, interaction, feature_name):
    """Check if a feature is enabled for the current guild"""
    if interaction.guild is None:
        return True  # Always allow in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

# Parse timeout duration from string like "1:30" (1 hour 30 minutes)
def parse_timeout_duration(duration_str: str) -> Optional[timedelta]:
    """Parse timeout duration from format HH:MM"""
    if not duration_str or duration_str == "00:00":
        return None
    
    pattern = re.compile(r'^(\d+):(\d{2})$')
    match = pattern.match(duration_str)
    
    if not match:
        return None
    
    hours = int(match.group(1))
    minutes = int(match.group(2))
    
    if hours == 0 and minutes == 0:
        return None
    
    return timedelta(hours=hours, minutes=minutes)

# Generate a unique ID for warnings
def generate_warning_id() -> str:
    """Generate a unique ID for a warning"""
    import uuid
    return str(uuid.uuid4())[:8]  # Use first 8 characters of UUID

# Setup function to register commands and event listeners
async def setup(bot: commands.Bot):
    # Extract feature name from the module name
    feature_name = __name__[8:] if __name__.startswith('command_') else __name__

    # Background task to check for unacknowledged warnings
    @bot.listen('on_ready')
    async def check_unacknowledged_warnings():
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                # Check all warnings
                for guild_id_str, guild_warnings in warning_data.warnings.items():
                    # Skip processing if feature is disabled for this guild
                    guild_id = int(guild_id_str)
                    guild = bot.get_guild(guild_id)
                    if not guild or not bot.is_feature_enabled(feature_name, guild_id):
                        continue
                    
                    for user_id_str, user_warnings in guild_warnings.items():
                        user_id = int(user_id_str)
                        member = guild.get_member(user_id)
                        
                        if not member:
                            continue  # Skip if user not in server
                            
                        # Check each warning
                        for warning in user_warnings:
                            # Skip already acknowledged warnings
                            if warning.get("acknowledged", False):
                                continue
                                
                            # Skip warnings that don't need acknowledgment
                            if not warning.get("requires_acknowledgment", True):
                                continue
                                
                            # Check if 24 hours have passed since warning
                            warning_time = datetime.datetime.fromisoformat(warning.get("timestamp"))
                            current_time = datetime.datetime.now()
                            
                            if (current_time - warning_time).total_seconds() >= 86400:  # 24 hours
                                # Apply timeout until user acknowledges
                                try:
                                    # Already timed out, skip
                                    if warning.get("timeout_applied", False):
                                        continue
                                        
                                    # Set timeout duration (28 days is max Discord allows)
                                    # Create a timezone-aware datetime
                                    if hasattr(discord.utils, 'utcnow'):
                                        # For newer Discord.py versions
                                        timeout_until = discord.utils.utcnow() + timedelta(days=28)
                                    else:
                                        # Fallback for older versions
                                        import datetime as dt
                                        timeout_until = datetime.datetime.now(dt.timezone.utc) + timedelta(days=28)
                                    
                                    # Check role hierarchy before attempting timeout
                                    if not guild.me.top_role > member.top_role:
                                        # Silently skip if bot's role is not high enough
                                        continue
                                        
                                    await member.timeout(
                                        timeout_until, 
                                        reason="Unacknowledged warning timeout"
                                    )
                                    
                                    # Mark warning as having applied a timeout
                                    warning["timeout_applied"] = True
                                    warning_data.save_data()
                                    
                                    # Try to DM the user
                                    try:
                                        embed = discord.Embed(
                                            title="⚠️ Automatic Timeout Applied",
                                            description=(
                                                f"You have been timed out in **{guild.name}** because you did not "
                                                f"acknowledge a warning within 24 hours.\n\n"
                                                f"To remove the timeout, please acknowledge the warning by reacting "
                                                f"with 👍 to the original warning message in your DMs."
                                            ),
                                            color=discord.Color.red()
                                        )
                                        embed.add_field(name="Reason for Original Warning", value=warning.get("reason", "No reason provided"))
                                        
                                        await member.send(embed=embed)
                                    except discord.Forbidden:
                                        # Couldn't DM the user - silently continue
                                        pass
                                    
                                except discord.Forbidden:
                                    # Silently handle permission errors without printing to console
                                    # Just mark that we tried to apply a timeout
                                    warning["timeout_attempted"] = True
                                    warning_data.save_data()
                                    pass
                                except Exception as e:
                                    # For other types of errors, log them without the full traceback
                                    print(f"Error in automatic timeout (Warning ID: {warning.get('id', 'unknown')}): {type(e).__name__}: {str(e)}")
            except Exception as e:
                print(f"Error in checking unacknowledged warnings: {type(e).__name__}: {str(e)}")
                
            # Check every 10 minutes
            await asyncio.sleep(600)

    @bot.tree.command(name="warn", description="Warn a user with optional timeout")
    @app_commands.describe(
        user="The user to warn",
        reason="The reason for the warning",
        timeout="Optional timeout duration in HH:MM format (e.g., 01:30 for 1 hour 30 minutes)"
    )
    @app_commands.default_permissions(moderate_members=True)
    async def warn_command(
        interaction: discord.Interaction, 
        user: discord.Member, 
        reason: str, 
        timeout: str = "00:00"
    ):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"The moderation system is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        # Prevent warning bot or self
        if user.id == bot.user.id:
            await interaction.response.send_message("I cannot be warned. I am above the law! 🤖", ephemeral=True)
            return
            
        if user.id == interaction.user.id:
            await interaction.response.send_message("You cannot warn yourself.", ephemeral=True)
            return
        
        # Prevent warning users with higher roles
        if interaction.guild and not interaction.user.guild_permissions.administrator:
            if user.top_role >= interaction.user.top_role:
                await interaction.response.send_message(
                    "You cannot warn someone with a higher or equal role than yourself.", 
                    ephemeral=True
                )
                return
        
        # Parse timeout duration if provided
        timeout_duration = parse_timeout_duration(timeout)
        
        # Create a timezone-aware datetime object for the timeout
        timeout_until = None
        timeout_applied = False
        
        # Defer the response to give us more time to process everything
        await interaction.response.defer()
        
        try:
            # Apply timeout if specified
            if timeout_duration and timeout_duration.total_seconds() > 0:
                if interaction.guild.me.guild_permissions.moderate_members:
                    # Use discord.utils.utcnow() to get a timezone-aware UTC datetime
                    if hasattr(discord.utils, 'utcnow'):
                        # For newer Discord.py versions
                        timeout_until = discord.utils.utcnow() + timeout_duration
                    else:
                        # Fallback for older versions - make a timezone-aware datetime
                        import datetime as dt
                        timeout_until = datetime.datetime.now(dt.timezone.utc) + timeout_duration
                    
                    try:
                        # Check if the bot's role is higher than the user's
                        if interaction.guild.me.top_role <= user.top_role:
                            await interaction.followup.send(
                                "I cannot timeout this user because their role is higher than or equal to mine.", 
                                ephemeral=True
                            )
                            # Continue with warning but without timeout
                        else:
                            await user.timeout(
                                timeout_until, 
                                reason=f"Warning: {reason}"
                            )
                            timeout_applied = True
                    except discord.Forbidden:
                        # Bot doesn't have permission to timeout
                        await interaction.followup.send(
                            "I don't have permission to timeout this user. The warning will be issued without a timeout.", 
                            ephemeral=True
                        )
                        # Continue with warning but without timeout
                else:
                    await interaction.followup.send(
                        "I don't have permission to timeout members. The warning will be issued without a timeout.", 
                        ephemeral=True
                    )
                    # Continue with warning but without timeout
            
            # Generate warning ID
            warning_id = generate_warning_id()
            
            # Create warning data object
            new_warning = {
                "id": warning_id,
                "guild_id": interaction.guild.id,
                "user_id": user.id,
                "issuer_id": interaction.user.id,
                "issuer_name": f"{interaction.user.name}",
                "reason": reason,
                "timestamp": datetime.datetime.now().isoformat(),
                "timeout_duration": str(timeout_duration) if timeout_duration else None,
                "timeout_until": timeout_until.isoformat() if timeout_until else None,
                "timeout_applied": timeout_applied,
                "acknowledged": False,
                "requires_acknowledgment": True
            }
            
            # Store the warning in local storage
            warning_data.add_warning(interaction.guild.id, user.id, new_warning)
            
            # Also store in CosmosDB for long-term storage
            await cosmos_warnings.save_warning(new_warning)
            
            # Create warning embed for server
            server_embed = discord.Embed(
                title="⚠️ User Warned",
                description=f"{user.mention} has been warned.",
                color=discord.Color.yellow()
            )
            server_embed.add_field(name="Reason", value=reason, inline=False)
            
            if timeout_duration and timeout_applied:
                server_embed.add_field(
                    name="Timeout Duration", 
                    value=f"{timeout_duration.seconds // 3600} hours, {(timeout_duration.seconds // 60) % 60} minutes",
                    inline=False
                )
            
            server_embed.set_footer(text=f"Warning ID: {warning_id} | Moderator: {interaction.user.name}")
            
            # Send warning in channel
            await interaction.followup.send(embed=server_embed)
            
            # Create DM embed with buttons
            dm_embed = discord.Embed(
                title="⚠️ Warning Notification",
                description=f"You have been warned in **{interaction.guild.name}**.",
                color=discord.Color.yellow()
            )
            dm_embed.add_field(name="Reason", value=reason, inline=False)
            
            if timeout_duration and timeout_applied:
                dm_embed.add_field(
                    name="Timeout Applied", 
                    value=f"{timeout_duration.seconds // 3600} hours, {(timeout_duration.seconds // 60) % 60} minutes",
                    inline=False
                )
            
            dm_embed.add_field(
                name="Required Action", 
                value="Please react with 👍 to acknowledge this warning. If you do not acknowledge within 24 hours, you may be timed out.",
                inline=False
            )
            
            dm_embed.set_footer(text=f"Warning ID: {warning_id} | Server: {interaction.guild.name}")
            
            # Create acknowledgment button
            class AcknowledgmentView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=None)  # No timeout
                
                @discord.ui.button(label="I don't agree with this warning", style=discord.ButtonStyle.danger, custom_id=f"disagree_{warning_id}")
                async def disagree_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    # Create a ticket based on disagreement
                    await create_disagreement_ticket(
                        bot, 
                        button_interaction, 
                        interaction.guild.id, 
                        user.id, 
                        warning_id,
                        interaction.user.id,
                        reason
                    )
                    
                    # Update the message
                    await button_interaction.response.send_message(
                        "Your disagreement has been registered and a ticket has been opened with the moderation team.",
                        ephemeral=True
                    )
            
            try:
                # Send DM with the embed and button
                dm_message = await user.send(embed=dm_embed, view=AcknowledgmentView())
                # Add thumbs up reaction for easy acknowledgment
                await dm_message.add_reaction("👍")
                
                # Rest of the function remains unchanged
                
            except discord.Forbidden:
                # User has DMs disabled
                try:
                    additional_info = discord.Embed(
                        title="⚠️ Unable to Send Direct Message",
                        description=f"Could not send a DM to {user.mention}. They may have DMs disabled.",
                        color=discord.Color.orange()
                    )
                    await interaction.followup.send(embed=additional_info)
                except discord.NotFound:
                    # If the interaction webhook is expired, try to send in the channel directly
                    try:
                        channel = interaction.channel
                        if channel:
                            await channel.send(
                                f"⚠️ Could not send a DM to {user.mention}. They may have DMs disabled.",
                                delete_after=10
                            )
                    except:
                        pass
                
                # Mark as not requiring acknowledgment since we couldn't send a DM
                guild_id_str = str(interaction.guild.id)
                user_id_str = str(user.id)
                
                if guild_id_str in warning_data.warnings and user_id_str in warning_data.warnings[guild_id_str]:
                    for warning in warning_data.warnings[guild_id_str][user_id_str]:
                        if warning.get("id") == warning_id:
                            warning["requires_acknowledgment"] = False
                            warning_data.save_data()
                            break
            
        except Exception as e:
            # More robust error handling
            try:
                error_message = f"An error occurred: {str(e)}"
                await interaction.followup.send(error_message, ephemeral=True)
            except discord.NotFound:
                # If the interaction webhook is expired, try to send in the channel directly
                try:
                    channel = interaction.channel
                    if channel:
                        await channel.send(
                            f"An error occurred while processing the warning: {str(e)}",
                            delete_after=10
                        )
                except:
                    # Last resort, just print to console
                    print(f"Error in warn command: {str(e)}")

    @bot.tree.command(name="warningcount", description="Check a user's warning history")
    @app_commands.describe(user="The user to check warnings for")
    @app_commands.default_permissions(moderate_members=True)
    async def warningcount_command(interaction: discord.Interaction, user: discord.Member):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"The moderation system is disabled. An administrator can enable it using `/setup`.",
                
            )
            return
        
        
        # Show that we're fetching the data
        await interaction.response.defer(ephemeral=True)
        
        # Get warnings from both local storage and CosmosDB for completeness
        local_warnings = warning_data.get_warnings(interaction.guild.id, user.id)
        cosmos_warnings_list = await cosmos_warnings.get_user_warnings(interaction.guild.id, user.id)
        
        # If no warnings in either system
        if not local_warnings and not cosmos_warnings_list:
            await interaction.followup.send(
                f"{user.mention} has no warnings in this server.",
                ephemeral=True
            )
            return
        
        # Create embed for warnings summary
        embed = discord.Embed(
            title=f"Warning History for {user.name}",
            description=f"Total warnings: {len(cosmos_warnings_list) or len(local_warnings)}",
            color=discord.Color.gold()
        )
        
        # Add a field showing warning count by reason (categorized)
        reason_counts = {}
        warnings_to_use = cosmos_warnings_list if cosmos_warnings_list else local_warnings
        
        for warning in warnings_to_use:
            reason = warning.get("reason", "No reason provided")
            # Truncate long reasons for categorization
            reason_key = reason[:50] + "..." if len(reason) > 50 else reason
            reason_counts[reason_key] = reason_counts.get(reason_key, 0) + 1
        
        # Add reason breakdown
        if reason_counts:
            reasons_text = "\n".join([f"• {count}× {reason}" for reason, count in reason_counts.items()])
            embed.add_field(name="Breakdown by Reason", value=reasons_text, inline=False)
        
        # Add recency information
        if warnings_to_use:
            # Sort by timestamp, newest first
            try:
                sorted_warnings = sorted(
                    warnings_to_use,
                    key=lambda w: datetime.datetime.fromisoformat(w.get("timestamp", "2000-01-01")),
                    reverse=True
                )
                newest = datetime.datetime.fromisoformat(sorted_warnings[0].get("timestamp"))
                oldest = datetime.datetime.fromisoformat(sorted_warnings[-1].get("timestamp"))
                
                time_span = newest - oldest
                days = time_span.days
                
                recent_period = datetime.datetime.now() - datetime.timedelta(days=30)
                recent_count = sum(1 for w in warnings_to_use if datetime.datetime.fromisoformat(w.get("timestamp", "2000-01-01")) > recent_period)
                
                embed.add_field(
                    name="Time Analysis", 
                    value=f"First warning: {oldest.strftime('%Y-%m-%d')}\nMost recent: {newest.strftime('%Y-%m-%d')}\nWarnings in last 30 days: {recent_count}",
                    inline=False
                )
                
                # Add warning frequency if multiple warnings
                if len(warnings_to_use) > 1 and days > 0:
                    frequency = len(warnings_to_use) / (days / 30)  # Warnings per month
                    embed.add_field(
                        name="Frequency", 
                        value=f"~{frequency:.1f} warnings per month",
                        inline=False
                    )
            except (ValueError, IndexError) as e:
                print(f"Error calculating warning timestamps: {e}")
        
        # Add acknowledgment stats
        acknowledged = sum(1 for w in warnings_to_use if w.get("acknowledged", False))
        if warnings_to_use:
            embed.add_field(
                name="Acknowledgment Rate", 
                value=f"{acknowledged}/{len(warnings_to_use)} warnings acknowledged ({acknowledged/len(warnings_to_use)*100:.0f}%)",
                inline=False
            )
        
        # Indicator if there have been timeouts
        timeout_count = sum(1 for w in warnings_to_use if w.get("timeout_applied", False))
        if timeout_count > 0:
            embed.add_field(
                name="Timeouts Applied", 
                value=f"{timeout_count} warnings resulted in timeouts",
                inline=False
            )
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(name="clearwarning", description="Clear a specific warning by ID")
    @app_commands.describe(
        user="The user whose warning to clear",
        warning_id="The ID of the warning to clear"
    )
    @app_commands.default_permissions(administrator=True)
    async def clearwarning_command(
        interaction: discord.Interaction, 
        user: discord.Member, 
        warning_id: str
    ):
        # Check if feature is enabled for this guild
        if not feature_check(bot, interaction, feature_name):
            await interaction.response.send_message(
                f"The moderation system is disabled. An administrator can enable it using `/setup`.",
                ephemeral=True
            )
            return
        
        guild_id_str = str(interaction.guild.id)
        user_id_str = str(user.id)
        
        if (guild_id_str not in warning_data.warnings or
            user_id_str not in warning_data.warnings[guild_id_str]):
            await interaction.response.send_message(
                f"{user.mention} has no warnings in this server.",
                ephemeral=True
            )
            return
        
        # Find and remove the warning
        found = False
        for i, warning in enumerate(warning_data.warnings[guild_id_str][user_id_str]):
            if warning.get("id") == warning_id:
                warning_data.warnings[guild_id_str][user_id_str].pop(i)
                found = True
                warning_data.save_data()
                
                # Also remove from CosmosDB
                try:
                    cosmos_warning_id = f"{interaction.guild.id}_{user.id}_{warning_id}"
                    await asyncio.to_thread(
                        lambda: cosmos_warnings.container.delete_item(
                            cosmos_warning_id, 
                            partition_key=str(interaction.guild.id)
                        )
                    )
                    print(f"Successfully deleted warning {warning_id} from CosmosDB")
                except exceptions.CosmosResourceNotFoundError:
                    print(f"Warning {warning_id} not found in CosmosDB")
                except Exception as e:
                    print(f"Error deleting warning from CosmosDB: {e}")
                    # Continue anyway since we've updated local storage
                    
                break
        
        if found:
            await interaction.response.send_message(
                f"Warning {warning_id} has been cleared for {user.mention}.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"No warning with ID {warning_id} found for {user.mention}.",
                ephemeral=True
            )

    async def create_disagreement_ticket(
        bot, 
        interaction: discord.Interaction, 
        guild_id: int, 
        user_id: int, 
        warning_id: str,
        mod_id: int,
        reason: str
    ):
        """Create a ticket for a warning disagreement"""
        try:
            guild = bot.get_guild(guild_id)
            if not guild:
                await interaction.response.send_message(
                    "Could not find the guild for this warning.",
                    ephemeral=True
                )
                return
            
            user = guild.get_member(user_id)
            mod = guild.get_member(mod_id)
            
            # Prepare embed for DM to specified users if no ticket category
            admin1_id = 723623196762570752
            admin2_id = 199635181236125697
            
            # Find or create ticket category
            tickets_category = discord.utils.get(guild.categories, name="TICKETS")
            
            if not tickets_category:
                # No ticket category, send DMs to specified users
                for admin_id in [admin1_id, admin2_id]:
                    try:
                        admin_user = await bot.fetch_user(admin_id)
                        if admin_user:
                            embed = discord.Embed(
                                title="⚠️ Warning Disagreement",
                                description=f"{user.mention} disagrees with a warning they received.",
                                color=discord.Color.red()
                            )
                            embed.add_field(name="Warning Reason", value=reason, inline=False)
                            embed.add_field(name="Warning ID", value=warning_id, inline=False)
                            embed.add_field(name="Moderator", value=f"{mod.mention if mod else 'Unknown'}", inline=False)
                            embed.add_field(name="Note", value="There is no 'TICKETS' category in the server for handling disputes.", inline=False)
                            
                            await admin_user.send(embed=embed)
                    except:
                        # Couldn't DM this admin, skip to next
                        continue
                
                # Notify user that admins have been contacted
                try:
                    member = guild.get_member(user_id)
                    if member:
                        await member.send("Server admins have been notified of your disagreement with this warning.")
                except discord.Forbidden:
                    # User has DMs closed, can't notify them
                    pass
            
            # Find "Support" and "Moderator" roles
            support_role = discord.utils.get(guild.roles, name="Support") or discord.utils.get(guild.roles, name="Supporter")
            mod_role = discord.utils.get(guild.roles, name="Moderator")
            
            # Generate a ticket number
            ticket_count = len([c for c in tickets_category.text_channels if c.name.startswith("dispute-")])
            ticket_name = f"dispute-{ticket_count + 1:04d}"
            
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
            channel = await guild.create_text_channel(
                name=ticket_name,
                category=tickets_category,
                overwrites=overwrites,
                topic=f"Warning dispute for {user.name} | Warning ID: {warning_id}"
            )
            
            # Create embed for the ticket
            embed = discord.Embed(
                title="Warning Disagreement Ticket",
                description=f"{user.mention} disagrees with a warning they received.",
                color=discord.Color.orange()
            )
            
            embed.add_field(name="Warning Reason", value=reason, inline=False)
            embed.add_field(name="Warning ID", value=warning_id, inline=False)
            embed.add_field(name="Moderator", value=f"{mod.mention if mod else 'Unknown'}", inline=False)
            
            # Create close button
            class CloseButton(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=None)
                
                @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket")
                async def close_button(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    if not button_interaction.user.guild_permissions.manage_channels:
                        await button_interaction.response.send_message(
                            "You don't have permission to close this ticket.",
                            ephemeral=True
                        )
                        return
                    
                    await button_interaction.response.send_message("Closing this ticket...")
                    await asyncio.sleep(3)
                    await channel.delete(reason="Ticket closed")
            
            # Tag relevant people
            mentions = []
            mentions.append(user.mention)
            
            if support_role:
                mentions.append(support_role.mention)
            
            if mod_role:
                mentions.append(mod_role.mention)
            
            if mod:
                mentions.append(mod.mention)
            
            # Send the initial message
            await channel.send(" ".join(mentions), embed=embed, view=CloseButton())
            
            # Notify the user about the ticket
            await interaction.followup.send(
                f"A ticket has been created for your warning disagreement: {channel.mention}",
                ephemeral=True
            )
            
        except Exception as e:
            print(f"Error creating disagreement ticket: {e}")
            await interaction.followup.send(
                "An error occurred while creating the ticket. Please contact a server administrator.",
                ephemeral=True
            )

    @bot.listen('on_raw_reaction_add')
    async def handle_warning_acknowledgment(payload):
        """Handle warning acknowledgment reactions"""
        # Ignore bot reactions
        if payload.user_id == bot.user.id:
            return
        
        # Only handle 👍 reactions
        if payload.emoji.name != '👍':
            return
        
        try:
            # Get the message
            channel = await bot.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            
            # Check if it's a DM channel and if the message is from the bot
            if isinstance(channel, discord.DMChannel) and message.author.id == bot.user.id:
                # Check if this is a warning message
                warning_id = None
                guild_id = None
                
                # Extract from footer if present
                if message.embeds and message.embeds[0].footer:
                    footer_text = message.embeds[0].footer.text
                    if "Warning ID:" in footer_text:
                        # Extract warning ID and guild info
                        warning_id_match = re.search(r"Warning ID: (\w+)", footer_text)
                        if warning_id_match:
                            warning_id = warning_id_match.group(1)
                        
                        # Get guild info from footer
                        guild_match = re.search(r"Server: (.+)$", footer_text)
                        if guild_match:
                            guild_name = guild_match.group(1)
                            # Find guild by name
                            for guild in bot.guilds:
                                if guild.name == guild_name:
                                    guild_id = guild.id
                                    break
                
                # If we found both guild ID and warning ID
                if guild_id and warning_id:
                    # Mark as acknowledged
                    user_id = payload.user_id
                    was_marked = warning_data.mark_acknowledged(guild_id, user_id, warning_id)
                    
                    # Also update in CosmosDB
                    try:
                        if cosmos_warnings.initialized:
                            # Try to find and update the item
                            cosmos_id = f"{guild_id}_{user_id}_{warning_id}"
                            
                            # Read the existing item
                            try:
                                existing_item = await asyncio.to_thread(
                                    lambda: cosmos_warnings.container.read_item(
                                        item=cosmos_id,
                                        partition_key=str(guild_id)
                                    )
                                )
                                
                                # Update the item
                                existing_item["acknowledged"] = True
                                existing_item["acknowledged_at"] = datetime.datetime.now().isoformat()
                                
                                # Save back
                                await asyncio.to_thread(
                                    lambda: cosmos_warnings.container.upsert_item(body=existing_item)
                                )
                                
                                print(f"Updated warning {warning_id} acknowledgment in CosmosDB")
                            except exceptions.CosmosResourceNotFoundError:
                                print(f"Warning {warning_id} not found in CosmosDB")
                            except Exception as e:
                                print(f"Error updating warning acknowledgment in CosmosDB: {e}")
                    except Exception as e:
                        print(f"Error with CosmosDB during acknowledgment: {e}")
                    
                    # If the warning was marked as acknowledged successfully
                    if was_marked:
                        # Check if the user has any active timeouts from this warning
                        guild = bot.get_guild(guild_id)
                        if guild:
                            member = guild.get_member(user_id)
                            if member and member.is_timed_out():
                                # Check if the timeout was due to this warning
                                user_warnings = warning_data.get_warnings(guild_id, user_id)
                                for warning in user_warnings:
                                    if warning.get("id") == warning_id and warning.get("timeout_applied", False):
                                        try:
                                            # Remove the timeout
                                            await member.timeout(None, reason="Warning acknowledged")
                                            
                                            # Notify the user
                                            await message.channel.send(
                                                "Thank you for acknowledging the warning. Your timeout has been removed.",
                                                reference=message
                                            )
                                        except discord.Forbidden:
                                            # Bot doesn't have permission to remove timeout
                                            pass
                                        break
                        
                        # Send acknowledgment confirmation
                        await message.add_reaction("✅")
                        
                        try:
                            await message.reply(
                                "Thank you for acknowledging this warning. This has been recorded."
                            )
                        except discord.Forbidden:
                            # Can't reply to the message for some reason
                            pass
        except Exception as e:
            print(f"Error handling warning acknowledgment: {e}")