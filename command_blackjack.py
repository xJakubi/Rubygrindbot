import discord
from discord.ext import commands
from discord import app_commands, ui
import asyncio
import random
import os
from typing import Dict, List, Optional, Tuple, Union
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import functools
import time
from enum import Enum
import warnings
# Module metadata for bot integration
DISPLAY_NAME = "Blackjack"
DESCRIPTION = "Play blackjack with XP as currency"
ENABLED_BY_DEFAULT = False

CARD_WIDTH = 85  
CARD_HEIGHT = 120 

# Constants for the game
MIN_BET = 10
MAX_BET = 1000
DEFAULT_BET = 50
BLACKJACK_PAYOUT = 1.5  # Blackjack pays 3:2
warnings.filterwarnings("ignore", message="Palette images with Transparency expressed in bytes should be converted to RGBA images")
# Path for card assets
CARD_ASSETS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "cards")
TABLE_BG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "table_bg.png")

# Create assets directories if they don't exist
os.makedirs(CARD_ASSETS_PATH, exist_ok=True)
os.makedirs(os.path.dirname(TABLE_BG_PATH), exist_ok=True)

# Card suits and values
SUITS = ["hearts", "diamonds", "clubs", "spades"]
CARD_VALUES = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "jack", "queen", "king", "ace"]

# Card values in blackjack
CARD_SCORES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10,
    "jack": 10, "queen": 10, "king": 10, "ace": 11  # Ace can be 1 or 11
}

# Game states
class GameState(Enum):
    WAITING_FOR_BET = 0
    PLAYER_TURN = 1
    DEALER_TURN = 2
    GAME_OVER = 3

# Helper function for feature check
def feature_check(bot, interaction, feature_name="blackjack"):
    """Check if blackjack is enabled for this guild"""
    if interaction.guild is None:
        return False  # Don't work in DMs
        
    if interaction.command and interaction.command.name == 'setup':
        return True  # Always allow setup command
        
    return bot.is_feature_enabled(feature_name, interaction.guild.id)

class Card:
    """Represents a playing card"""
    def __init__(self, suit: str, value: str):
        self.suit = suit
        self.value = value
        
    def __str__(self):
        return f"{self.value.capitalize()} of {self.suit.capitalize()}"
    
    @property
    def score(self) -> int:
        return CARD_SCORES[self.value]
    
    @property
    def image_filename(self) -> str:
        return f"{self.value}_of_{self.suit}.png"
    
    @property
    def image_path(self) -> str:
        return os.path.join(CARD_ASSETS_PATH, self.suit, f"{self.value}.png")

class Deck:
    """Represents a deck of cards"""
    def __init__(self):
        self.cards = []
        self.reset()
        
    def reset(self):
        """Reset the deck with all 52 cards"""
        self.cards = []
        for suit in SUITS:
            for value in CARD_VALUES:
                self.cards.append(Card(suit, value))
        self.shuffle()
        
    def shuffle(self):
        """Shuffle the deck"""
        random.shuffle(self.cards)
        
    def deal(self) -> Card:
        """Deal one card from the deck"""
        if not self.cards:
            self.reset()
        return self.cards.pop()

class Hand:
    """Represents a hand of cards"""
    def __init__(self):
        self.cards = []
        
    def add_card(self, card: Card):
        """Add a card to the hand"""
        self.cards.append(card)
        
    @property
    def score(self) -> int:
        """Calculate the score of the hand, accounting for aces"""
        score = sum(card.score for card in self.cards)
        
        # Adjust for aces
        num_aces = sum(1 for card in self.cards if card.value == "ace")
        while score > 21 and num_aces > 0:
            score -= 10  # Convert an ace from 11 to 1
            num_aces -= 1
            
        return score
    
    @property
    def is_blackjack(self) -> bool:
        """Check if this is a blackjack (21 with 2 cards)"""
        return len(self.cards) == 2 and self.score == 21
    
    @property
    def is_bust(self) -> bool:
        """Check if the hand is bust (over 21)"""
        return self.score > 21
    
    def __str__(self) -> str:
        """String representation of the hand"""
        return ", ".join(str(card) for card in self.cards)

class BlackjackGame:
    """Represents a blackjack game session"""
    def __init__(self, player_id: int):
        self.player_id = player_id
        self.deck = Deck()
        self.player_hand = Hand()
        self.dealer_hand = Hand()
        self.bet = DEFAULT_BET
        self.state = GameState.WAITING_FOR_BET
        self.last_activity = time.time()
        
    def start_game(self) -> None:
        """Deal initial cards to start the game"""
        self.player_hand = Hand()
        self.dealer_hand = Hand()
        
        # Deal 2 cards to player and dealer
        self.player_hand.add_card(self.deck.deal())
        self.dealer_hand.add_card(self.deck.deal())
        self.player_hand.add_card(self.deck.deal())
        self.dealer_hand.add_card(self.deck.deal())
        
        self.state = GameState.PLAYER_TURN
        self.last_activity = time.time()
        
    def hit(self) -> Card:
        """Add a card to the player's hand"""
        card = self.deck.deal()
        self.player_hand.add_card(card)
        self.last_activity = time.time()
        return card
        
    def stand(self) -> List[Card]:
        """Player stands, dealer plays their hand"""
        self.state = GameState.DEALER_TURN
        dealt_cards = []
        
        # Dealer hits until they have at least 17
        while self.dealer_hand.score < 17:
            card = self.deck.deal()
            self.dealer_hand.add_card(card)
            dealt_cards.append(card)
            
        self.state = GameState.GAME_OVER
        self.last_activity = time.time()
        return dealt_cards
    
    def determine_winner(self) -> Tuple[str, int]:
        """Determine the winner and payout"""
        if self.player_hand.is_bust:
            return "DEALER", -self.bet
            
        if self.dealer_hand.is_bust:
            return "PLAYER", self.bet  # This should be the bet amount
            
        if self.player_hand.is_blackjack and not self.dealer_hand.is_blackjack:
            return "BLACKJACK", int(self.bet * BLACKJACK_PAYOUT)
            
        if self.dealer_hand.is_blackjack and not self.player_hand.is_blackjack:
            return "DEALER", -self.bet
            
        if self.player_hand.score > self.dealer_hand.score:
            return "PLAYER", self.bet  # This should be the bet amount
            
        if self.dealer_hand.score > self.player_hand.score:
            return "DEALER", -self.bet
            
        return "PUSH", 0  # It's a tie

# Storage for active games
active_games = {}
def load_card_image(image_path):
    """Load a card image and handle transparency properly"""
    try:
        image = Image.open(image_path)
        # Convert palette images with transparency to RGBA
        if image.mode == 'P':
            image = image.convert('RGBA')
        elif image.mode != 'RGBA':
            image = image.convert('RGBA')
        return image.resize((CARD_WIDTH, CARD_HEIGHT))
    except Exception as e:
        print(f"Error loading image {image_path}: {e}")
        return None
# Function to download card assets if needed
async def download_card_assets():
    """Download card assets if they don't exist locally"""
    card_exists = os.path.exists(os.path.join(CARD_ASSETS_PATH, "hearts", "ace.png"))
    bg_exists = os.path.exists(TABLE_BG_PATH)
    
    if card_exists and bg_exists:
        return True
    
    # Create directories for each suit
    for suit in SUITS:
        os.makedirs(os.path.join(CARD_ASSETS_PATH, suit), exist_ok=True)
    
    try:
        # Base URL for card assets (using a public card deck API)
        base_url = "https://deckofcardsapi.com/static/img/"
        card_codes = {
            "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7", "8": "8", "9": "9", "10": "0",
            "jack": "J", "queen": "Q", "king": "K", "ace": "A"
        }
        suit_codes = {"hearts": "H", "diamonds": "D", "clubs": "C", "spades": "S"}
        
        async with aiohttp.ClientSession() as session:
            # Download each card
            for suit in SUITS:
                for value in CARD_VALUES:
                    code = f"{card_codes[value]}{suit_codes[suit]}.png"
                    url = base_url + code
                    path = os.path.join(CARD_ASSETS_PATH, suit, f"{value}.png")
                    
                async with session.get(url) as resp:
                    if resp.status == 200:
                        img_data = await resp.read()
                        # Convert to RGBA immediately
                        try:
                            img = Image.open(BytesIO(img_data))
                            if img.mode == 'P':
                                img = img.convert('RGBA')
                            img.save(path)
                        except Exception:
                            # Fallback to just saving the raw data
                            with open(path, 'wb') as f:
                                f.write(img_data)
            
            # Download table background
            if not bg_exists:
                # Green felt table background
                bg_url = "https://www.transparenttextures.com/patterns/pool-table.png"
                async with session.get(bg_url) as resp:
                    if resp.status == 200:
                        with open(TABLE_BG_PATH, 'wb') as f:
                            f.write(await resp.read())
                    else:
                        # Create a simple green background if download fails
                        img = Image.new('RGB', (800, 500), color=(0, 102, 0))
                        img.save(TABLE_BG_PATH)
        
        return True
    except Exception as e:
        print(f"Error downloading card assets: {e}")
        return False

async def render_game_image(game: BlackjackGame, show_dealer_hole: bool = False) -> BytesIO:
    """Render the current game state as an image"""
    try:
        # Create a new image for the table
        table_img = Image.new('RGB', (800, 500), color=(0, 102, 0))
        
        # Try to load the background image
        try:
            bg = Image.open(TABLE_BG_PATH)
            table_img.paste(bg.resize((800, 500)), (0, 0))
        except:
            pass  # Use the default green if background can't be loaded
        
        draw = ImageDraw.Draw(table_img)
        
        # Try to load font, use default if not available
        try:
            font = ImageFont.truetype("arial.ttf", 27)
            small_font = ImageFont.truetype("arial.ttf", 23)
        except:
            font = ImageFont.load_default()
            small_font = ImageFont.load_default()
        
        # Draw dealer's hand
        draw.text((400, 50), "Dealer", fill=(0, 0, 0), font=font, anchor="mm")
        
        # Position cards
        dealer_card_x = 300
        player_card_x = 300
        
        # Draw dealer's cards
        for i, card in enumerate(game.dealer_hand.cards):  # Changed player_hand to dealer_hand
            # Hide the hole card if needed
            if i == 1 and not show_dealer_hole and game.state == GameState.PLAYER_TURN:
                # Draw card back
                card_img = Image.new('RGB', (80, 120), color=(180, 0, 0))
                draw_card = ImageDraw.Draw(card_img)
                draw_card.rectangle([(5, 5), (75, 115)], outline=(255, 255, 255), width=2)
                draw_card.text((40, 60), "?", fill=(255, 255, 255), font=font, anchor="mm")
            else:

                    # Convert to RGBA mode to handle transparency properly
                try:
                    card_img = load_card_image(card.image_path)
                    if card_img is None:
                        raise Exception("Failed to load card image")
                except:
                    # Create a placeholder if image not found
                    card_img = Image.new('RGB', (80, 120), color=(255, 255, 255))
                    draw_card = ImageDraw.Draw(card_img)
                    
                    # Use larger font for card value
                    try:
                        value_font = ImageFont.truetype("arial.ttf", 24)
                        suit_font = ImageFont.truetype("arial.ttf", 20)
                    except:
                        value_font = font
                        suit_font = small_font
                        
                    # Draw with black text
                    draw_card.text((40, 50), f"{card.value.upper()}", fill=(0, 0, 0), font=value_font, anchor="mm")
                    
                    # Draw suit symbol instead of text
                    suit_symbol = {"hearts": "♥", "diamonds": "♦", "clubs": "♣", "spades": "♠"}
                    suit_color = (0, 0, 0)
                    draw_card.text((40, 80), suit_symbol.get(card.suit, card.suit), fill=suit_color, font=suit_font, anchor="mm")
                
            table_img.paste(card_img, (dealer_card_x, 80))  # Changed to dealer position
            dealer_card_x += 90  # Changed to dealer_card_x
        
        # Draw dealer's score if not hiding cards
        if show_dealer_hole or game.state != GameState.PLAYER_TURN:
            draw.text((400, 220), f"Score: {game.dealer_hand.score}", 
                    fill=(0, 0, 0), font=font, anchor="mm")
        
        # Draw player's hand
        draw.text((400, 250), "Player", fill=(0, 0, 0), font=font, anchor="mm")
        
        # Draw player's cards
        for card in game.player_hand.cards:
            try:
                card_img = load_card_image(card.image_path)
                if card_img is None:
                    raise Exception("Failed to load card image")
            except:
                # Create a placeholder if image not found
                card_img = Image.new('RGB', (80, 120), color=(255, 255, 255))
                draw_card = ImageDraw.Draw(card_img)
                
                # Use larger font for card value
                try:
                    value_font = ImageFont.truetype("arial.ttf", 24)
                    suit_font = ImageFont.truetype("arial.ttf", 20)
                except:
                    value_font = font
                    suit_font = small_font
                    
                # Draw with black text
                draw_card.text((40, 50), f"{card.value.upper()}", fill=(0, 0, 0), font=value_font, anchor="mm")
                
                # Draw suit symbol instead of text
                suit_symbol = {"hearts": "♥", "diamonds": "♦", "clubs": "♣", "spades": "♠"}
                suit_color = (0, 0, 0)
                draw_card.text((40, 80), suit_symbol.get(card.suit, card.suit), fill=suit_color, font=suit_font, anchor="mm")
            
            table_img.paste(card_img, (player_card_x, 280))
            player_card_x += 90
        
        # Draw player's score
        draw.text((400, 420), f"Score: {game.player_hand.score}", 
                fill=(0, 0, 0), font=font, anchor="mm")
        
        # Draw bet amount
        draw.text((700, 450), f"Bet: {game.bet} XP", 
                fill=(0, 0, 0), font=font, anchor="mm")
        
        # Create BytesIO object to save the image
        img_byte_arr = BytesIO()
        table_img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        
        return img_byte_arr
    
    except Exception as e:
        print(f"Error rendering game image: {e}")
        # Return a simple error image
        error_img = Image.new('RGB', (800, 500), color=(255, 0, 0))
        draw = ImageDraw.Draw(error_img)
        draw.text((400, 250), "Error rendering game image", 
                  fill=(255, 255, 255), font=ImageFont.load_default(), anchor="mm")
        
        img_byte_arr = BytesIO()
        error_img.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        
        return img_byte_arr

class BlackjackView(ui.View):
    """Interactive view for the blackjack game"""
    def __init__(self, user_id: int, game: BlackjackGame, exp_system):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
        self.game = game
        self.exp_system = exp_system
        self.message = None
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the user interacting is the one who started the game"""
        is_owner = interaction.user.id == self.user_id
        if not is_owner:
            await interaction.response.send_message(
                "This is not your game. Start your own with `/blackjack`.", 
                ephemeral=True
            )
            return False
        return True
    
    async def on_timeout(self):
        """Handle timeout - end the game and refund half bet if not completed"""
        if self.game.state != GameState.GAME_OVER:
            # Refund half bet if game not completed
            refund = self.game.bet // 2
            if refund > 0 and self.exp_system:
                await self.exp_system.update_user_xp(
                    int(self.message.guild.id), self.user_id, refund
                )
            
            # Update message
            embed = discord.Embed(
                title="Blackjack Game Timed Out",
                description=f"Game abandoned. {refund} XP returned.",
                color=discord.Color.red()
            )
            
            try:
                await self.message.edit(embed=embed, view=None)
            except:
                pass
            
        # Clean up the game
        if self.user_id in active_games:
            del active_games[self.user_id]
    
    @ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit_button(self, interaction: discord.Interaction, button: ui.Button):
        """Hit - take another card"""
        # Player takes a card
        card = self.game.hit()
        
        if self.game.player_hand.is_bust:
            await self.end_game(interaction)
        else:
            # Update the game image
            game_image = await render_game_image(self.game)
            file = discord.File(game_image, filename="blackjack.png")
            
            # Update embed
            embed = discord.Embed(
                title="Blackjack Game",
                description=(
                    f"You drew a **{card}**.\n"
                    f"Your score: **{self.game.player_hand.score}**"
                ),
                color=discord.Color.green()
            )
            embed.set_image(url="attachment://blackjack.png")
            
            await interaction.response.edit_message(embed=embed, attachments=[file])
    
    @ui.button(label="Stand", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def stand_button(self, interaction: discord.Interaction, button: ui.Button):
        """Stand - keep current hand and end turn"""
        await self.end_game(interaction)
    
    @ui.button(label="Double Down", style=discord.ButtonStyle.green, emoji="💰")
    async def double_down_button(self, interaction: discord.Interaction, button: ui.Button):
        """Double the bet, take exactly one more card, then stand"""
        # Only allow double down on initial hand (when player has exactly 2 cards)
        if len(self.game.player_hand.cards) > 2:
            await interaction.response.send_message(
                "You can only double down on your initial hand!",
                ephemeral=True
            )
            return
            
        # Check if player has enough XP
        if self.exp_system:
            user_data = await self.exp_system.get_user_xp(
                int(interaction.guild_id), self.user_id
            )
            user_xp = user_data.get('xp', 0)
            
            if user_xp < self.game.bet:
                await interaction.response.send_message(
                    f"You don't have enough XP to double down! You need {self.game.bet} more XP.",
                    ephemeral=True
                )
                return
            
            # Double the bet
            self.game.bet *= 2
            
            # Take exactly one card
            card = self.game.hit()
            
            # Then stand (end the game)
            await self.end_game(interaction)
        else:
            await interaction.response.send_message(
                "Error: XP system not available.", 
                ephemeral=True
            )
    
    async def end_game(self, interaction: discord.Interaction):
        """Finish the game, determine winner and payouts"""
        # Dealer plays their hand
        dealer_cards = self.game.stand()
        
        # Determine winner
        winner, payout = self.game.determine_winner()
        
        # Process XP change if exp_system is available
        xp_change_text = ""
        if self.exp_system and payout != 0:
            await self.exp_system.update_user_xp(
                int(interaction.guild_id), self.user_id, payout
            )
            
            if payout > 0:
                xp_change_text = f"🎉 You **won {payout} XP**!"
            else:
                xp_change_text = f"😢 You **lost {abs(payout)} XP**."
        
        # Generate result message
        if winner == "PLAYER":
            result_text = "You win! 🎉"
        elif winner == "BLACKJACK":
            result_text = "Blackjack! You win! 🎉🎉"
        elif winner == "DEALER":
            result_text = "Dealer wins! 😢"
        else:  # PUSH
            result_text = "Push! It's a tie! 🤝"
        
        # Update the game image with all cards visible
        game_image = await render_game_image(self.game, show_dealer_hole=True)
        file = discord.File(game_image, filename="blackjack.png")
        
        # Create result embed
        embed = discord.Embed(
            title="Blackjack Game Finished",
            description=(
                f"**{result_text}**\n\n"
                f"Your score: **{self.game.player_hand.score}**\n"
                f"Dealer score: **{self.game.dealer_hand.score}**\n\n"
                f"{xp_change_text}"
            ),
            color=discord.Color.gold()
        )
        embed.set_image(url="attachment://blackjack.png")
        
        # Update the message
        await interaction.response.edit_message(embed=embed, attachments=[file], view=None)
        
        # Clean up the game
        if self.user_id in active_games:
            del active_games[self.user_id]

class BetModal(ui.Modal, title='Place Your Bet'):
    """Modal for entering bet amount"""
    bet = ui.TextInput(
        label='Bet Amount (XP)',
        placeholder=f'Enter amount between {MIN_BET} and {MAX_BET}',
        required=True,
        min_length=1,
        max_length=4,
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

async def setup(bot: commands.Bot):
    """Set up the blackjack game"""
    # Load exp_system module for XP management
    exp_system = None
    try:
        # Try to import the exp_system module
        import command_expsystem
        exp_system = command_expsystem
    except ImportError:
        print("Warning: Experience system not found. Blackjack will use its own XP tracking.")
    
    # Download card assets in the background
    bot.loop.create_task(download_card_assets())
    
    @bot.tree.command(name="blackjack", description="Play a game of blackjack with XP")
    async def blackjack_command(interaction: discord.Interaction):
        # Check if feature is enabled
        if not feature_check(bot, interaction):
            await interaction.response.send_message(
                "Blackjack is disabled on this server. An admin can enable it using `/setup`.",
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
        
        # Get user XP if exp_system is available
        max_bet = MAX_BET
        if exp_system:
            try:
                user_data = await exp_system.get_user_xp(
                    int(interaction.guild_id), user_id
                )
                user_xp = user_data.get('xp', 0)
                max_bet = min(MAX_BET, user_xp)
                
                if user_xp < MIN_BET:
                    await interaction.response.send_message(
                        f"You need at least {MIN_BET} XP to play blackjack!", 
                        ephemeral=True
                    )
                    return
            except Exception as e:
                print(f"Error accessing XP system: {e}")
        
        # Show bet modal
        modal = BetModal(max_bet)
        await interaction.response.send_modal(modal)
        

            
     
        try:
            await modal.wait()
            
            # Check if bet is valid using the stored bet_amount
            if modal.bet_amount is None:
                return  # Invalid bet, error already shown
            
            bet_amount = modal.bet_amount
            
            # Deduct the bet from user's XP
            if exp_system:
                await exp_system.update_user_xp(
                    int(interaction.guild_id), user_id, -bet_amount
                )
            
            # Create a new game
            game = BlackjackGame(user_id)
            game.bet = bet_amount
            active_games[user_id] = game
            
            # Start the game
            game.start_game()
            
            # Generate the initial game image
            game_image = await render_game_image(game)
            file = discord.File(game_image, filename="blackjack.png")
            
            # Create initial embed
            embed = discord.Embed(
                title="Blackjack Game",
                description=(
                    f"Your bet: **{bet_amount} XP**\n\n"
                    f"Your cards: {game.player_hand}\n"
                    f"Your score: **{game.player_hand.score}**\n\n"
                    f"Dealer shows: **{game.dealer_hand.cards[0]}**"
                ),
                color=discord.Color.green()
            )
            embed.set_image(url="attachment://blackjack.png")
            
            # Create the game view
            view = BlackjackView(user_id, game, exp_system)
            
            # Send the initial game state
            response = await interaction.followup.send(embed=embed, file=file, view=view)
            view.message = response
            
            # Auto-resolve blackjack
            if game.player_hand.is_blackjack:
                await view.end_game(interaction)
            
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "Bet not submitted in time. Try again!", ephemeral=True
            )
    

