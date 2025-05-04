import discord
from discord import app_commands
from discord import ui
import random
import asyncio
import time
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import math
from typing import Optional, Dict, List, Tuple
import warnings

# Module metadata for bot integration
DISPLAY_NAME = "Crash Game"
DESCRIPTION = "Gamble your XP in a thrilling Crash game with increasing multipliers"
ENABLED_BY_DEFAULT = False

# Suppress PIL warning about palette images
warnings.filterwarnings("ignore", message="Palette images with Transparency expressed in bytes should be converted to RGBA images")

# Constants
MIN_BET = 10
MAX_BET = 5000
DEFAULT_BET = 100

# Crash settings
MIN_MULTIPLIER = 1.0     # Minimum crash point (1.0x)
HOUSE_EDGE = 0.05        # 5% house edge
BASE_CRASH_VALUE = 0.99  # Used in crash point calculation

# Store active games
active_games = {}

# Helper function to check if feature is enabled
def feature_check(bot, interaction):
    """Check if the feature is enabled in the server"""
    feature_name = "crash"  # Feature name extracted from file name
    if interaction.guild is None:
        return False  # Don't work in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

class CrashGame:
    """Represents a single crash game"""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.bet = DEFAULT_BET
        self.crash_point = self.calculate_crash_point()
        self.current_multiplier = 1.0
        self.is_cashed_out = False
        self.is_crashed = False
        self.start_time = None
        
    def calculate_crash_point(self) -> float:
        """Calculate the crash point using house edge"""
        # Using a formula that creates a house edge
        r = random.random()
        # House edge applied to crash point calculation
        if r < HOUSE_EDGE:
            return MIN_MULTIPLIER  # Guaranteed minimum crash
        else:
            # Formula to create exponential distribution with house edge
            return max(MIN_MULTIPLIER, (1 / (r * (1 - HOUSE_EDGE) * BASE_CRASH_VALUE)))
            
    def get_current_multiplier(self) -> float:
        """Get the current multiplier based on elapsed time"""
        if self.is_crashed:
            return self.crash_point
        
        if not self.start_time:
            return 1.0
            
        elapsed = time.time() - self.start_time
        # Exponential growth function for multiplier
        current = 1.0 + 0.05 * math.exp(elapsed * 0.17)
        
        # Check if crashed
        if current >= self.crash_point:
            self.is_crashed = True
            return self.crash_point
            
        return current
        
    def cash_out(self) -> bool:
        """Cash out at the current multiplier"""
        if self.is_crashed or self.is_cashed_out:
            return False
            
        self.is_cashed_out = True
        return True

    def start(self):
        """Start the game"""
        self.start_time = time.time()
        
class BetModal(ui.Modal, title='Place Your Bet'):
    """Modal for entering bet amount"""
    bet = ui.TextInput(
        label='Bet Amount (XP)',
        placeholder=f'Enter amount between {MIN_BET} and {MAX_BET}',
        required=True,
        min_length=1,
        max_length=5,
        default=str(DEFAULT_BET)
    )

    def __init__(self, max_bet: int):
        super().__init__()
        self.bet.placeholder = f'Enter amount between {MIN_BET} and {max_bet}'
        self.bet_amount = None  # Store the validated bet amount here
        self.max_bet = max_bet

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bet_amount = int(self.bet.value)
            if bet_amount < MIN_BET:
                await interaction.response.send_message(
                    f"Minimum bet is {MIN_BET} XP!", ephemeral=True
                )
                self.bet_amount = None
            elif bet_amount > self.max_bet:
                await interaction.response.send_message(
                    f"Maximum bet is {self.max_bet} XP!", ephemeral=True
                )
                self.bet_amount = None
            else:
                self.bet_amount = bet_amount
                await interaction.response.defer()  # Important: defer the response
        except ValueError:
            await interaction.response.send_message(
                "Please enter a valid number!", ephemeral=True
            )
            self.bet_amount = None

async def render_crash_image(multiplier: float, is_crashed: bool = False) -> BytesIO:
    """Generate a crash game image"""
    # Create image
    width, height = 800, 400
    graph_img = Image.new('RGB', (width, height), color=(18, 18, 20))
    draw = ImageDraw.Draw(graph_img)
    
    try:
        # Try to load font
        font = ImageFont.truetype("arial.ttf", 48)
        small_font = ImageFont.truetype("arial.ttf", 24)
    except:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()
        
    # Set background grid
    grid_color = (30, 30, 34)
    for i in range(0, width, 50):
        draw.line([(i, 0), (i, height)], fill=grid_color, width=1)
    for i in range(0, height, 50):
        draw.line([(0, i), (width, i)], fill=grid_color, width=1)
    
    # Generate crash curve points
    points = []
    max_x = min(100, max(20, int(multiplier * 10)))
    for x in range(max_x + 1):
        # Exponential curve
        y = 1.0 + 0.05 * math.exp(x * 0.17)
        if y > multiplier and is_crashed:
            y = multiplier
        points.append((x * 7, height - int(y * 50)))
    
    # Draw curve
    if len(points) > 1:
        color = (255, 60, 60) if is_crashed else (60, 255, 128)
        for i in range(len(points) - 1):
            draw.line([points[i], points[i+1]], fill=color, width=3)
    
    # Draw multiplier text
    multiplier_text = f"{multiplier:.2f}x"
    text_color = (255, 60, 60) if is_crashed else (60, 255, 128)
    draw.text((width // 2, height // 2), multiplier_text, 
              fill=text_color, font=font, anchor="mm")
    
    # Add "CRASHED!" text if crashed
    if is_crashed:
        draw.text((width // 2, height // 2 - 60), "CRASHED!", 
                  fill=(255, 60, 60), font=font, anchor="mm")
    
    # Save to BytesIO
    img_byte_arr = BytesIO()
    graph_img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    
    return img_byte_arr

class CrashView(ui.View):
    """Button view for the Crash game"""
    def __init__(self, user_id: int, game: CrashGame, exp_system):
        super().__init__(timeout=120)  # 2 minute timeout
        self.user_id = user_id
        self.game = game
        self.exp_system = exp_system
        self.message = None
        self.update_task = None
        self.stopped = False
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the user interacting is the game owner"""
        is_owner = str(interaction.user.id) == str(self.user_id)
        if not is_owner:
            await interaction.response.send_message(
                "This is not your game. Start your own with `/crash`.", 
                ephemeral=True
            )
            return False
        return True
        
    async def on_timeout(self):
        """Handle timeout"""
        self.stopped = True
        if self.update_task and not self.update_task.done():
            self.update_task.cancel()
            
        if self.game.is_crashed or self.game.is_cashed_out:
            return
            
        try:
            # Game abandoned without cashing out - they lose
            embed = discord.Embed(
                title="Crash Game Timed Out",
                description=f"Game abandoned. You lost {self.game.bet} XP.",
                color=discord.Color.red()
            )
            await self.message.edit(embed=embed, view=None)
        except:
            pass
            
        # Clean up the game
        if self.user_id in active_games:
            del active_games[self.user_id]
            
    @ui.button(label="Cash Out", style=discord.ButtonStyle.green, emoji="💰")
    async def cash_out_button(self, interaction: discord.Interaction, button: ui.Button):
        """Cash out at the current multiplier"""
        if self.game.is_crashed:
            await interaction.response.send_message(
                "Too late! The game has already crashed.", ephemeral=True
            )
            return
            
        if self.game.is_cashed_out:
            await interaction.response.send_message(
                "You've already cashed out!", ephemeral=True
            )
            return
            
        # Cash out successful
        success = self.game.cash_out()
        if success:
            # Stop the updates
            self.stopped = True
            if self.update_task and not self.update_task.done():
                    self.update_task.cancel()
                    
                # Calculate winnings
            current_multiplier = self.game.get_current_multiplier()
            winnings = int(self.game.bet * current_multiplier)
            profit = winnings - self.game.bet
                
                # Add winnings to user's XP using the existing exp_system
            try:
                await self.exp_system.update_user_xp(
                    int(interaction.guild_id), self.user_id, winnings  # Change from profit to winnings
                )
            except Exception as e:
                    print(f"Error updating XP on cash out: {e}")
                
            # Update image and embed
            game_image = await render_crash_image(current_multiplier, False)
            file = discord.File(game_image, filename="crash.png")
            
            embed = discord.Embed(
                title="Crash Game - Cashed Out!",
                description=(
                    f"You cashed out at **{current_multiplier:.2f}x**\n\n"
                    f"Initial bet: **{self.game.bet} XP**\n"
                    f"Winnings: **{winnings} XP**\n"
                    f"Profit: **{profit} XP**"
                ),
                color=discord.Color.green()
            )
            embed.set_image(url="attachment://crash.png")
            
            button.disabled = True
            button.label = f"Cashed Out at {current_multiplier:.2f}x"
            
            await interaction.response.edit_message(
                embed=embed, attachments=[file], view=self
            )
            
            # Clean up
            if self.user_id in active_games:
                del active_games[self.user_id]
                
    async def start_game_updates(self):
        """Start updating the game state periodically"""
        try:
            while not self.stopped:
                # Skip updates if game is finished
                if self.game.is_crashed or self.game.is_cashed_out:
                    break
                    
                # Get current multiplier
                multiplier = self.game.get_current_multiplier()
                
                # Generate new image
                game_image = await render_crash_image(
                    multiplier, self.game.is_crashed
                )
                file = discord.File(game_image, filename="crash.png")
                
                # Update embed
                embed = discord.Embed(
                    title="Crash Game",
                    description=(
                        f"Current Multiplier: **{multiplier:.2f}x**\n\n"
                        f"Your bet: **{self.game.bet} XP**\n"
                        f"Potential win: **{int(self.game.bet * multiplier)} XP**\n\n"
                        f"Click **Cash Out** to take your winnings now!"
                    ),
                    color=discord.Color.gold() if not self.game.is_crashed else discord.Color.red()
                )
                embed.set_image(url="attachment://crash.png")
                
                # Edit message with new content
                try:
                    await self.message.edit(
                        embed=embed, attachments=[file]
                    )
                except Exception as e:
                    print(f"Error updating crash game: {e}")
                    break
                    
                # If crashed, disable button and end game
                if self.game.is_crashed:
                    self.cash_out_button.disabled = True
                    self.cash_out_button.label = "Game Crashed!"
                    self.cash_out_button.style = discord.ButtonStyle.danger
                    
                    # Update one last time with crashed state
                    embed.title = "Crash Game - Crashed!"
                    embed.description = (
                        f"Crashed at **{multiplier:.2f}x**!\n\n"
                        f"Your bet: **{self.game.bet} XP**\n"
                        f"Loss: **{self.game.bet} XP**\n\n"
                        f"Better luck next time!"
                    )
                    embed.color = discord.Color.red()
                    
                    try:
                        await self.message.edit(
                            embed=embed, attachments=[file], view=self
                        )
                    except:
                        pass
                        
                    # Clean up
                    if self.user_id in active_games:
                        del active_games[self.user_id]
                    break
                
                # Wait before updating again (faster updates at higher multipliers)
                update_delay = max(0.1, 1.0 - (multiplier / 20))  # Gets faster as multiplier increases
                await asyncio.sleep(update_delay)
                
        except asyncio.CancelledError:
            pass  # Task was cancelled, exit gracefully
        except Exception as e:
            print(f"Error in crash game update loop: {e}")

async def setup(bot):
    """Setup the crash game command"""
    # Import the exp_system module
    import command_expsystem as exp_system
    
    @bot.tree.command(name="crash", description="Play a game of Crash with XP")
    async def crash_command(interaction: discord.Interaction):
        """Play a game of Crash with your XP"""
        # Check if feature is enabled
        if not feature_check(bot, interaction):
            await interaction.response.send_message(
                "Crash game is disabled on this server. An admin can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        user_id = interaction.user.id
        
        # Check if user already has a game in progress
        if user_id in active_games:
            await interaction.response.send_message(
                "You already have a game in progress!", ephemeral=True
            )
            return
            
        # Get user XP from the existing exp_system
        max_bet = MAX_BET
        try:
            user_data = await exp_system.get_user_xp(
                int(interaction.guild_id), user_id
            )
            user_xp = user_data.get('xp', 0)
            max_bet = min(MAX_BET, user_xp)
            
            if user_xp < MIN_BET:
                await interaction.response.send_message(
                    f"You need at least {MIN_BET} XP to play Crash!", 
                    ephemeral=True
                )
                return
        except Exception as e:
            print(f"Error accessing XP system: {e}")
            await interaction.response.send_message(
                "Error accessing XP system. Please try again later.", 
                ephemeral=True
            )
            return
                
        # Show bet modal
        modal = BetModal(max_bet)
        await interaction.response.send_modal(modal)
        
        try:
            await modal.wait()
            
            # Check if bet is valid
            if modal.bet_amount is None:
                return  # Invalid bet, error already shown
                
            bet_amount = modal.bet_amount
            
            # Deduct the bet from user's XP using the existing exp_system
            try:
                await exp_system.update_user_xp(
                    int(interaction.guild_id), user_id, -bet_amount
                )
            except Exception as e:
                print(f"Error deducting XP for bet: {e}")
                await interaction.followup.send(
                    "Error processing your bet. Please try again later.", 
                    ephemeral=True
                )
                return
                
            # Create a new game
            game = CrashGame(user_id)
            game.bet = bet_amount
            active_games[user_id] = game
            
            # Initial game image
            game_image = await render_crash_image(1.0)
            file = discord.File(game_image, filename="crash.png")
            
            # Create initial embed
            embed = discord.Embed(
                title="Crash Game - Starting",
                description=(
                    f"Your bet: **{bet_amount} XP**\n"
                    f"Current Multiplier: **1.00x**\n\n"
                    f"Game starting in 3..."
                ),
                color=discord.Color.blue()
            )
            embed.set_image(url="attachment://crash.png")
            
            # Send the initial message
            response = await interaction.followup.send(embed=embed, file=file)
            
            # Countdown 3...2...1
            for i in range(2, 0, -1):
                await asyncio.sleep(1)
                embed.description = (
                    f"Your bet: **{bet_amount} XP**\n"
                    f"Current Multiplier: **1.00x**\n\n"
                    f"Game starting in {i}..."
                )
                await interaction.followup.edit_message(response.id, embed=embed)
                
            # Create game view
            view = CrashView(user_id, game, exp_system)
            
            # Start the game
            game.start()
            
            # Final countdown message
            embed.title = "Crash Game"
            embed.description = (
                f"Your bet: **{bet_amount} XP**\n"
                f"Current Multiplier: **1.00x**\n\n"
                f"Click **Cash Out** to take your winnings!"
            )
            embed.color = discord.Color.gold()
            
            # Send the game view with buttons
            updated = await interaction.followup.edit_message(
                response.id,
                embed=embed,
                view=view
            )
            view.message = updated
            
            # Start periodic updates
            view.update_task = asyncio.create_task(view.start_game_updates())
            
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Bet not submitted in time. Try again!", ephemeral=True
            )
            
    @bot.tree.command(name="crash_stats", description="View Crash game statistics")
    async def crash_stats_command(interaction: discord.Interaction):
        """View Crash game statistics"""
        # Check if feature is enabled
        if not feature_check(bot, interaction):
            await interaction.response.send_message(
                "Crash game is disabled on this server. An admin can enable it using `/setup`.",
                ephemeral=True
            )
            return
            
        await interaction.response.send_message(
            "Crash game statistics coming soon!", ephemeral=True
        )