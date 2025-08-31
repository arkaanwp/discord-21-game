import discord
from discord.ext import commands
import random
import asyncio
import os
import logging
import json
import time
from typing import Optional, Dict, Any, Tuple, Union
from dataclasses import dataclass
from enum import Enum

# ==================== CONFIGURATION ====================

# Logging setup
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment setup
def setup_environment():
    """Setup environment variables with proper error handling"""
    try:
        from dotenv import load_dotenv, dotenv_values
        
        # Try different encodings for .env file
        encodings = ['utf-8', 'utf-8-sig', 'latin-1']
        for encoding in encodings:
            try:
                load_dotenv(encoding=encoding)
                if os.getenv('DISCORD_TOKEN'):
                    break
                    
                # Fallback manual loading
                env_vars = dotenv_values('.env')
                for key, value in env_vars.items():
                    clean_key = key.lstrip('\ufeff')
                    os.environ[clean_key] = value
                    
            except UnicodeDecodeError:
                continue
                
    except ImportError:
        logger.warning("python-dotenv not installed. Using system environment variables.")
    except Exception as e:
        logger.error(f"Environment setup error: {e}")

setup_environment()

# Configuration constants
@dataclass
class Config:
    CHANNEL_ID: int = int(os.getenv('STARTUP_CHANNEL_ID', 0))
    OWNER_ID: int = int(os.getenv('BOT_OWNER_ID', 0))
    GAME_TIMEOUT: int = int(os.getenv('GAME_TIMEOUT', 60))
    MAX_CARD_VALUE: int = int(os.getenv('MAX_CARD_VALUE', 11))
    STARTING_HP: int = int(os.getenv('STARTING_HP', 7))
    STATS_FILE: str = "game_stats.json"
    BOT_PREFIX: str = "#"

config = Config()

# ==================== ENUMS & DATA CLASSES ====================

class GameEndReason(Enum):
    REVEAL = "reveal"
    BUST = "bust"
    TIMEOUT = "timeout"
    DECK_EMPTY = "deck_empty"

class GameStatus(Enum):
    THINKING = "🤔 Thinking"
    READY = "✅ Ready"
    BUST = "💥 BUST"

class MatchStatus(Enum):
    ACTIVE = "active"
    ELIMINATION = "elimination"
    REMATCH_PENDING = "rematch_pending"

@dataclass
class PlayerData:
    """Clean player data structure"""
    user: discord.Member
    cards: list[int]
    continued: bool = False
    
    @property
    def total(self) -> int:
        return sum(self.cards)
    
    @property
    def is_bust(self) -> bool:
        return self.total > 21
    
    @property
    def score(self) -> int:
        return self.total if not self.is_bust else 0

@dataclass
class MatchPlayerData:
    """HP and match data for players"""
    user: discord.Member
    hp: int = config.STARTING_HP
    
    @property
    def is_eliminated(self) -> bool:
        return self.hp <= 0

@dataclass
class MatchData:
    """Complete match data with HP system"""
    player1: MatchPlayerData
    player2: MatchPlayerData
    game_number: int = 1
    status: MatchStatus = MatchStatus.ACTIVE
    channel: discord.TextChannel = None
    
    @property
    def current_bet(self) -> int:
        return self.game_number
    
    @property
    def has_elimination(self) -> bool:
        return self.player1.is_eliminated or self.player2.is_eliminated
    
    @property
    def winner(self) -> Optional[MatchPlayerData]:
        if self.player1.is_eliminated:
            return self.player2
        elif self.player2.is_eliminated:
            return self.player1
        return None
    
    def get_player_data(self, user_id: int) -> Optional[MatchPlayerData]:
        """Get match player data by user ID"""
        if self.player1.user.id == user_id:
            return self.player1
        elif self.player2.user.id == user_id:
            return self.player2
        return None
    
    def get_opponent_data(self, user_id: int) -> Optional[MatchPlayerData]:
        """Get opponent match data by user ID"""
        if self.player1.user.id == user_id:
            return self.player2
        elif self.player2.user.id == user_id:
            return self.player1
        return None

# ==================== STATISTICS MANAGER ====================

class StatsManager:
    """Enhanced statistics management with match points"""
    
    def __init__(self, stats_file: str):
        self.stats_file = stats_file
        self._cache: Dict[str, Dict[str, int]] = {}
        self._cache_dirty = False
        self.load_stats()
    
    def load_stats(self) -> None:
        """Load stats from file with caching"""
        try:
            with open(self.stats_file, 'r') as f:
                self._cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._cache = {}
        self._cache_dirty = False
    
    def save_stats(self) -> None:
        """Save stats only if cache is dirty"""
        if not self._cache_dirty:
            return
            
        try:
            with open(self.stats_file, 'w') as f:
                json.dump(self._cache, f, indent=2)
            self._cache_dirty = False
        except Exception as e:
            logger.error(f"Failed to save stats: {e}")
    
    def get_user_stats(self, user_id: int) -> Dict[str, int]:
        """Get user statistics"""
        return self._cache.get(str(user_id), {
            'wins': 0, 
            'losses': 0, 
            'match_wins': 0, 
            'match_losses': 0
        })
    
    def update_game_result(self, winner_id: int, loser_id: int) -> None:
        """Update single game statistics"""
        winner_key, loser_key = str(winner_id), str(loser_id)
        
        # Initialize if not exists
        if winner_key not in self._cache:
            self._cache[winner_key] = {'wins': 0, 'losses': 0, 'match_wins': 0, 'match_losses': 0}
        if loser_key not in self._cache:
            self._cache[loser_key] = {'wins': 0, 'losses': 0, 'match_wins': 0, 'match_losses': 0}
        
        # Update game stats
        self._cache[winner_key]['wins'] += 1
        self._cache[loser_key]['losses'] += 1
        self._cache_dirty = True
        
        logger.info(f"Game stats updated: Winner {winner_id}, Loser {loser_id}")
    
    def update_match_result(self, winner_id: int, loser_id: int) -> None:
        """Update match statistics"""
        winner_key, loser_key = str(winner_id), str(loser_id)
        
        # Initialize if not exists
        if winner_key not in self._cache:
            self._cache[winner_key] = {'wins': 0, 'losses': 0, 'match_wins': 0, 'match_losses': 0}
        if loser_key not in self._cache:
            self._cache[loser_key] = {'wins': 0, 'losses': 0, 'match_wins': 0, 'match_losses': 0}
        
        # Update match stats
        self._cache[winner_key]['match_wins'] += 1
        self._cache[loser_key]['match_losses'] += 1
        self._cache_dirty = True
        
        logger.info(f"Match stats updated: Winner {winner_id}, Loser {loser_id}")

# Global stats manager
stats_manager = StatsManager(config.STATS_FILE)

# ==================== GAME STATE MANAGEMENT ====================

class GameState:
    """Enhanced game state with HP system integration"""
    
    def __init__(self, player1: discord.Member, player2: discord.Member, channel: discord.TextChannel, match_data: MatchData):
        self.player1 = PlayerData(player1, [])
        self.player2 = PlayerData(player2, [])
        self.channel = channel
        self.match_data = match_data
        
        # Initialize deck and deal cards
        self.deck = list(range(1, config.MAX_CARD_VALUE + 1))
        random.shuffle(self.deck)
        self.player1.cards = [self.deck.pop()]
        self.player2.cards = [self.deck.pop()]
        
        # Game state
        self.current_turn_id = player1.id
        self.turn_start_time = time.time()
        self.public_message: Optional[discord.Message] = None
        
        # Tasks for cleanup
        self._tasks: list[asyncio.Task] = []
    
    def get_player_data(self, user_id: int) -> Optional[PlayerData]:
        """Get player data by user ID"""
        if self.player1.user.id == user_id:
            return self.player1
        elif self.player2.user.id == user_id:
            return self.player2
        return None
    
    def get_opponent_data(self, user_id: int) -> Optional[PlayerData]:
        """Get opponent data by user ID"""
        if self.player1.user.id == user_id:
            return self.player2
        elif self.player2.user.id == user_id:
            return self.player1
        return None
    
    def get_current_player(self) -> PlayerData:
        """Get current turn player"""
        return self.get_player_data(self.current_turn_id)
    
    def reset_turn_timer(self) -> None:
        """Reset turn timer to full duration"""
        self.turn_start_time = time.time()
    
    def switch_turn(self) -> None:
        """Switch to next player's turn with full timer reset"""
        self.current_turn_id = (
            self.player2.user.id 
            if self.current_turn_id == self.player1.user.id 
            else self.player1.user.id
        )
        self.reset_turn_timer()
    
    def add_task(self, task: asyncio.Task) -> None:
        """Add task for cleanup tracking"""
        self._tasks.append(task)
    
    def cleanup(self) -> None:
        """Clean up all resources"""
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()
    
    @property
    def remaining_time(self) -> float:
        """Get remaining time for current turn"""
        elapsed = time.time() - self.turn_start_time
        return max(0, config.GAME_TIMEOUT - elapsed)
    
    @property
    def both_continued(self) -> bool:
        """Check if both players have continued"""
        return self.player1.continued and self.player2.continued

# Game and match storage
active_games: Dict[frozenset, GameState] = {}
active_matches: Dict[frozenset, MatchData] = {}

# ==================== ERROR HANDLING UTILITY ====================

async def handle_command_error(interaction: discord.Interaction, error: Exception, command_name: str):
    """Centralized error handling for commands"""
    logger.error(f"Error in {command_name}: {error}")
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ An error occurred. Please try again.", 
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ An error occurred. Please try again.", 
                ephemeral=True
            )
    except:
        pass

# ==================== EMBED CREATORS ====================

class EmbedCreator:
    """Enhanced embed creation with HP system"""
    
    @staticmethod
    def create_help_embed() -> discord.Embed:
        """Create help embed with HP system explanation"""
        embed = discord.Embed(
            title="🎲 Twenty One Bot - HP Battle System",
            description="A card game where you battle with HP! Get as close to 21 as possible without going over!",
            color=discord.Color.blue()
        )
        
        fields = [
            ("🎯 How to Play", 
             "Get cards totaling as close to 21 as possible without going over. "
             "You start with 1 hidden card, then take more cards that become visible to opponents.", False),
            ("❤️ HP Battle System",
             f"• Both players start with **{config.STARTING_HP} HP**\n"
             "• Game 1 bets **1 HP**, Game 2 bets **2 HP**, etc.\n"
             "• Winner takes HP, loser loses HP\n"
             "• First to reach **0 HP or less** is eliminated!\n"
             "• Match winner gets **1 Match Point**", False),
            ("🚀 Start Match", "`/play @opponent` - Challenge another player to HP battle", False),
            ("🎴 Game Controls", 
             "**🃏 View Cards** - Click to see your current hand\n"
             "**🎲 Take Card** - Draw another card (adds to visible cards)\n"
             "**✋ Stay** - Keep current cards and end your turn", False),
            ("❓ Other Commands", 
             "`/help` - Show this help message\n"
             "`/profil @user` - View player statistics\n"
             "`/stats` - View your own statistics", False)
        ]
        
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        
        embed.set_footer(text="Battle until elimination! Use buttons for all interactions!")
        return embed
    
    @staticmethod
    def create_private_hand_embed(player_data: PlayerData) -> discord.Embed:
        """Create private hand embed for player"""
        color = discord.Color.red() if player_data.is_bust else discord.Color.green()
        embed = discord.Embed(title="🃏 Your Hand 🃏", color=color)
        
        cards_display = " • ".join([f"**{card}**" for card in player_data.cards])
        embed.add_field(name="Cards", value=cards_display, inline=False)
        
        total_text = f"**{player_data.total}**"
        if player_data.is_bust:
            total_text += " - BUST! 💥"
        embed.add_field(name="Total", value=total_text, inline=False)
        
        if not player_data.is_bust:
            if player_data.total == 21:
                embed.add_field(name="Status", value="🎯 **PERFECT 21!**", inline=False)
            elif player_data.total > 18:
                embed.add_field(name="Status", value="⚠️ Getting risky...", inline=False)
        
        embed.set_footer(text="Only you can see this message.")
        return embed
    
    @staticmethod
    def create_game_embed(game_state: GameState) -> discord.Embed:
        """Create public game embed with HP display"""
        p1, p2 = game_state.player1, game_state.player2
        current_player = game_state.get_current_player()
        match_data = game_state.match_data
        
        embed = discord.Embed(
            title=f"🎲 HP Battle - Game {match_data.game_number}",
            description=f"⚔️ {p1.user.mention} **VS** {p2.user.mention}",
            color=discord.Color.gold()
        )
        
        # HP Display
        p1_match = match_data.get_player_data(p1.user.id)
        p2_match = match_data.get_player_data(p2.user.id)
        
        embed.add_field(
            name="❤️ Health Points",
            value=f"{p1.user.mention}: **{p1_match.hp} HP**\n{p2.user.mention}: **{p2_match.hp} HP**",
            inline=False
        )
        
        embed.add_field(
            name="💰 Current Bet",
            value=f"**{match_data.current_bet} HP** at stake this game!",
            inline=False
        )
        
        # Show visible cards (first card hidden, rest visible)
        def get_card_display(player_data: PlayerData) -> str:
            visible_cards = [f'**{card}**' for card in player_data.cards[1:]]
            visible_text = ' • '.join(visible_cards)
            return f"**[?]**{' • ' + visible_text if visible_text else ''}"
        
        embed.add_field(
            name=f"🎴 {p1.user.display_name}'s Cards", 
            value=get_card_display(p1), 
            inline=True
        )
        embed.add_field(
            name=f"🎴 {p2.user.display_name}'s Cards", 
            value=get_card_display(p2), 
            inline=True
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)
        
        # Player status
        p1_status = GameStatus.READY.value if p1.continued else GameStatus.THINKING.value
        p2_status = GameStatus.READY.value if p2.continued else GameStatus.THINKING.value
        
        embed.add_field(
            name="📊 Status",
            value=f"{p1.user.mention}: {p1_status}\n{p2.user.mention}: {p2_status}",
            inline=False
        )
        
        # Timer display
        remaining_time = game_state.remaining_time
        minutes = int(remaining_time // 60)
        seconds = int(remaining_time % 60)
        timer_icon = "🔴" if remaining_time <= 15 else "⏰"
        
        embed.add_field(
            name="⏳ Timer",
            value=f"{timer_icon} **{minutes}:{seconds:02d}** remaining",
            inline=False
        )
        
        embed.set_footer(
            text=f"🎯 Current turn: {current_player.user.display_name}",
            icon_url=current_player.user.display_avatar.url
        )
        return embed
    
    @staticmethod
    def create_endgame_embed(
        game_state: GameState, 
        reason: GameEndReason, 
        winner: Optional[discord.Member] = None, 
        timed_out_player: Optional[discord.Member] = None
    ) -> discord.Embed:
        """Create endgame results embed with HP changes"""
        p1, p2 = game_state.player1, game_state.player2
        match_data = game_state.match_data
        
        embed = discord.Embed(title=f"🏁 Game {match_data.game_number} Over!", color=discord.Color.gold())
        
        # Set description based on end reason
        if reason == GameEndReason.BUST:
            embed.description = f"💥 **{winner.mention}** wins! Opponent went bust!"
        elif reason == GameEndReason.TIMEOUT:
            embed.description = f"⏰ **{winner.mention}** wins! {timed_out_player.mention} timed out."
        elif reason == GameEndReason.DECK_EMPTY:
            embed.description = "🃏 Deck is empty! Game ends in a draw."
        else:  # REVEAL
            if p1.score > p2.score:
                embed.description = f"🎉 **{p1.user.mention}** wins with {p1.score}!"
            elif p2.score > p1.score:
                embed.description = f"🎉 **{p2.user.mention}** wins with {p2.score}!"
            else:
                embed.description = "🤝 **It's a tie!** Both players have the same score."
        
        # Show final hands
        def get_status_text(player_data: PlayerData) -> str:
            return "💥 BUST" if player_data.is_bust else f"✅ {player_data.total}"
        
        def get_cards_text(player_data: PlayerData) -> str:
            return " • ".join([f"**{card}**" for card in player_data.cards])
        
        embed.add_field(
            name=f"🎴 {p1.user.display_name}'s Final Hand",
            value=f"{get_cards_text(p1)}\n**Total: {get_status_text(p1)}**",
            inline=False
        )
        embed.add_field(
            name=f"🎴 {p2.user.display_name}'s Final Hand",
            value=f"{get_cards_text(p2)}\n**Total: {get_status_text(p2)}**",
            inline=False
        )
        
        # Show HP changes
        p1_match = match_data.get_player_data(p1.user.id)
        p2_match = match_data.get_player_data(p2.user.id)
        
        embed.add_field(
            name="❤️ HP After This Game",
            value=f"{p1.user.mention}: **{p1_match.hp} HP**\n{p2.user.mention}: **{p2_match.hp} HP**",
            inline=False
        )
        
        # Show what happens next
        if match_data.has_elimination:
            eliminated_player = p1.user if p1_match.is_eliminated else p2.user
            embed.add_field(
                name="🚨 ELIMINATION!",
                value=f"💀 **{eliminated_player.mention}** has been eliminated!\n"
                      f"🏆 Match winner will be decided soon!",
                inline=False
            )
        else:
            embed.add_field(
                name="🔄 Next Game",
                value=f"⏳ **Game {match_data.game_number + 1}** will start soon!\n"
                      f"💰 Next bet: **{match_data.game_number + 1} HP**",
                inline=False
            )
        
        return embed
    
    @staticmethod
    def create_elimination_embed(match_data: MatchData) -> discord.Embed:
        """Create elimination/match end embed"""
        winner = match_data.winner
        eliminated = match_data.player1 if match_data.player1.is_eliminated else match_data.player2
        
        embed = discord.Embed(
            title="💀 ELIMINATION! Match Over!",
            description=f"🏆 **{winner.user.mention}** WINS THE MATCH!\n💀 **{eliminated.user.mention}** has been eliminated!",
            color=discord.Color.red()
        )
        
        embed.add_field(
            name="🎯 Match Results",
            value=f"**Winner**: {winner.user.mention} with **{winner.hp} HP** remaining\n"
                  f"**Eliminated**: {eliminated.user.mention} with **{eliminated.hp} HP**",
            inline=False
        )
        
        embed.add_field(
            name="📊 Match Summary", 
            value=f"**Total Games Played**: {match_data.game_number}\n"
                  f"**Match Points Earned**: 1 point to {winner.user.mention}",
            inline=False
        )
        
        embed.add_field(
            name="🔄 What's Next?",
            value="Click **✅ Rematch** to start a new HP battle!\n"
                  "Click **❌ Exit** to end the session.",
            inline=False
        )
        
        return embed
    
    @staticmethod
    def create_profile_embed(user: discord.Member, user_stats: Dict[str, int]) -> discord.Embed:
        """Create enhanced profile statistics embed"""
        embed = discord.Embed(
            title=f"📊 Game Profile - {user.display_name}",
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        
        if not any(user_stats.values()):
            embed.description = "This player hasn't played any games yet."
        else:
            # Game stats
            wins = user_stats.get('wins', 0)
            losses = user_stats.get('losses', 0)
            total_games = wins + losses
            win_rate = (wins / total_games * 100) if total_games > 0 else 0
            
            # Match stats  
            match_wins = user_stats.get('match_wins', 0)
            match_losses = user_stats.get('match_losses', 0)
            total_matches = match_wins + match_losses
            match_win_rate = (match_wins / total_matches * 100) if total_matches > 0 else 0
            
            embed.add_field(name="🎮 Game Statistics", value="\u200b", inline=False)
            embed.add_field(name="🏆 Game Wins", value=f"**{wins}**", inline=True)
            embed.add_field(name="💔 Game Losses", value=f"**{losses}**", inline=True)
            embed.add_field(name="📈 Game Win Rate", value=f"**{win_rate:.1f}%**", inline=True)
            
            embed.add_field(name="⚔️ Match Statistics", value="\u200b", inline=False)
            embed.add_field(name="🏅 Match Wins", value=f"**{match_wins}**", inline=True)
            embed.add_field(name="💀 Match Losses", value=f"**{match_losses}**", inline=True)
            embed.add_field(name="🎯 Match Win Rate", value=f"**{match_win_rate:.1f}%**", inline=True)
            
            embed.add_field(
                name="📊 Total Summary",
                value=f"**Games Played**: {total_games}\n**Matches Played**: {total_matches}",
                inline=False
            )
        
        return embed

# ==================== UI COMPONENTS ====================

class GameView(discord.ui.View):
    """Enhanced view with game action buttons"""
    
    def __init__(self, game_key: frozenset):
        super().__init__(timeout=None)
        self.game_key = game_key
    
    @discord.ui.button(label="🃏 View Cards", style=discord.ButtonStyle.secondary, custom_id="view_cards")
    async def view_cards(self, interaction: discord.Interaction, button: discord.ui.Button):
        """View cards button callback"""
        try:
            player_id = interaction.user.id
            
            # Validate game exists
            if self.game_key not in active_games:
                await interaction.response.send_message(
                    "❌ This game is no longer active!", 
                    ephemeral=True
                )
                return
            
            game_state = active_games[self.game_key]
            player_data = game_state.get_player_data(player_id)
            
            # Validate player
            if not player_data:
                await interaction.response.send_message(
                    "❌ You're not a player in this game!", 
                    ephemeral=True
                )
                return
            
            # Send hand embed
            embed = EmbedCreator.create_private_hand_embed(player_data)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            logger.info(f"View Cards used by {interaction.user}")
            
        except Exception as e:
            logger.error(f"View cards error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Error viewing cards. Try again.", 
                    ephemeral=True
                )
            except:
                pass
    
    @discord.ui.button(label="🎲 Take Card", style=discord.ButtonStyle.primary, custom_id="take_card")
    async def take_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Take card button callback"""
        try:
            player_id = interaction.user.id
            
            # Validate game exists
            if self.game_key not in active_games:
                await interaction.response.send_message(
                    "❌ This game is no longer active!", 
                    ephemeral=True
                )
                return
            
            game_state = active_games[self.game_key]
            
            # Validate player is in game
            player_data = game_state.get_player_data(player_id)
            if not player_data:
                await interaction.response.send_message(
                    "❌ You're not a player in this game!", 
                    ephemeral=True
                )
                return
            
            # Validate it's player's turn
            if player_id != game_state.current_turn_id:
                await interaction.response.send_message(
                    "❌ It's not your turn!", 
                    ephemeral=True
                )
                return
            
            # Validate player hasn't continued
            if player_data.continued:
                await interaction.response.send_message(
                    "❌ You already chose to stay! Cannot take more cards.", 
                    ephemeral=True
                )
                return
            
            # Validate deck has cards
            if not game_state.deck:
                await interaction.response.send_message("❌ The deck is empty!", ephemeral=True)
                await GameManager.end_game(self.game_key, GameEndReason.DECK_EMPTY)
                return
            
            # Cancel current tasks
            game_state.cleanup()
            
            # Deal card
            new_card = game_state.deck.pop()
            player_data.cards.append(new_card)
            
            # Reset timer for same player after taking card
            game_state.reset_turn_timer()
            
            # Send updated hand
            embed = EmbedCreator.create_private_hand_embed(player_data)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Check for bust
            if player_data.is_bust:
                opponent_data = game_state.get_opponent_data(player_id)
                await GameManager.end_game(self.game_key, GameEndReason.BUST, winner=opponent_data.user)
                logger.info(f"{interaction.user} went bust with {player_data.total}")
            else:
                # Create new tasks with fresh timer
                timer_task = await GameManager.create_timer_task(self.game_key)
                updater_task = await GameManager.create_display_updater_task(self.game_key)
                game_state.add_task(timer_task)
                game_state.add_task(updater_task)
                
                # Update display immediately to show fresh timer
                await GameManager.update_public_embed(self.game_key)
                
            logger.info(f"Take Card used by {interaction.user}, drew {new_card}")
            
        except Exception as e:
            logger.error(f"Take card error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Error taking card. Try again.", 
                    ephemeral=True
                )
            except:
                pass
    
    @discord.ui.button(label="✋ Stay", style=discord.ButtonStyle.success, custom_id="stay")
    async def stay(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Stay/continue button callback"""
        try:
            player_id = interaction.user.id
            
            # Validate game exists
            if self.game_key not in active_games:
                await interaction.response.send_message(
                    "❌ This game is no longer active!", 
                    ephemeral=True
                )
                return
            
            game_state = active_games[self.game_key]
            
            # Validate player is in game
            player_data = game_state.get_player_data(player_id)
            if not player_data:
                await interaction.response.send_message(
                    "❌ You're not a player in this game!", 
                    ephemeral=True
                )
                return
            
            # Validate it's player's turn
            if player_id != game_state.current_turn_id:
                await interaction.response.send_message(
                    "❌ It's not your turn!", 
                    ephemeral=True
                )
                return
            
            # Validate player hasn't already continued
            if player_data.continued:
                await interaction.response.send_message(
                    "❌ You already chose to stay!", 
                    ephemeral=True
                )
                return
            
            # Cancel current tasks
            game_state.cleanup()
            
            # Set continued status
            player_data.continued = True
            await interaction.response.send_message(
                "✅ You chose to stay with your current cards. Turn passes to opponent.", 
                ephemeral=True
            )
            
            # Check if both players continued
            if game_state.both_continued:
                await GameManager.end_game(self.game_key, GameEndReason.REVEAL)
                logger.info(f"Game {self.game_key} ended - both players stayed")
            else:
                # Switch turn (this will reset timer automatically)
                game_state.switch_turn()
                
                # Create new tasks with fresh timer
                timer_task = await GameManager.create_timer_task(self.game_key)
                updater_task = await GameManager.create_display_updater_task(self.game_key)
                game_state.add_task(timer_task)
                game_state.add_task(updater_task)
                
                # Update display immediately to show fresh timer
                await GameManager.update_public_embed(self.game_key)
            
            logger.info(f"Stay used by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Stay error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Error processing stay. Try again.", 
                    ephemeral=True
                )
            except:
                pass

class RematchView(discord.ui.View):
    """Rematch/Exit view after elimination"""
    
    def __init__(self, match_key: frozenset):
        super().__init__(timeout=300)  # 5 minute timeout
        self.match_key = match_key
    
    @discord.ui.button(label="✅ Rematch", style=discord.ButtonStyle.success, custom_id="rematch")
    async def rematch(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Start new match button callback"""
        try:
            player_id = interaction.user.id
            
            # Validate match exists
            if self.match_key not in active_matches:
                await interaction.response.send_message(
                    "❌ This match is no longer available!", 
                    ephemeral=True
                )
                return
            
            match_data = active_matches[self.match_key]
            player_match_data = match_data.get_player_data(player_id)
            
            # Validate player is in match
            if not player_match_data:
                await interaction.response.send_message(
                    "❌ You're not a player in this match!", 
                    ephemeral=True
                )
                return
            
            # Reset HP and start new match
            match_data.player1.hp = config.STARTING_HP
            match_data.player2.hp = config.STARTING_HP
            match_data.game_number = 1
            match_data.status = MatchStatus.ACTIVE
            
            # Start first game
            await MatchManager.start_new_game(self.match_key)
            
            await interaction.response.send_message(
                f"🔄 **Rematch started!** Both players reset to {config.STARTING_HP} HP. Good luck!",
                ephemeral=True
            )
            
            logger.info(f"Rematch started by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Rematch error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Error starting rematch. Try again.", 
                    ephemeral=True
                )
            except:
                pass
    
    @discord.ui.button(label="❌ Exit", style=discord.ButtonStyle.danger, custom_id="exit")
    async def exit_match(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Exit match button callback"""
        try:
            player_id = interaction.user.id
            
            # Validate match exists
            if self.match_key not in active_matches:
                await interaction.response.send_message(
                    "❌ This match is no longer available!", 
                    ephemeral=True
                )
                return
            
            match_data = active_matches[self.match_key]
            player_match_data = match_data.get_player_data(player_id)
            
            # Validate player is in match
            if not player_match_data:
                await interaction.response.send_message(
                    "❌ You're not a player in this match!", 
                    ephemeral=True
                )
                return
            
            # Clean up match
            await MatchManager.end_match(self.match_key)
            
            await interaction.response.send_message(
                "👋 **Match ended!** Thanks for playing. Use `/play` to start a new battle!",
                ephemeral=True
            )
            
            # Update message to show match ended
            embed = discord.Embed(
                title="🏁 Match Session Ended",
                description="Players have left the battle. Use `/play` to start a new HP battle!",
                color=discord.Color.greyple()
            )
            
            try:
                await interaction.edit_original_response(embed=embed, view=None)
            except:
                pass
            
            logger.info(f"Match exited by {interaction.user}")
            
        except Exception as e:
            logger.error(f"Exit match error: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Error exiting match. Try again.", 
                    ephemeral=True
                )
            except:
                pass

# ==================== GAME MANAGEMENT ====================

class GameManager:
    """Enhanced game management with HP system"""
    
    @staticmethod
    async def update_public_embed(game_key: frozenset) -> None:
        """Update the public game embed"""
        if game_key not in active_games:
            return
            
        game_state = active_games[game_key]
        if not game_state.public_message:
            return
        
        try:
            embed = EmbedCreator.create_game_embed(game_state)
            view = GameView(game_key)
            await game_state.public_message.edit(embed=embed, view=view)
        except discord.NotFound:
            logger.warning(f"Public message not found for game {game_key}")
        except Exception as e:
            logger.error(f"Error updating public embed: {e}")
    
    @staticmethod
    async def end_game(
        game_key: frozenset, 
        reason: GameEndReason, 
        winner: Optional[discord.Member] = None, 
        timed_out_player: Optional[discord.Member] = None
    ) -> None:
        """End game with proper HP system and match continuation logic"""
        if game_key not in active_games:
            return
        
        game_state = active_games.pop(game_key)
        match_data = game_state.match_data
        
        # Determine final winner/loser for this game
        final_winner, final_loser = None, None
        
        if reason in [GameEndReason.BUST, GameEndReason.TIMEOUT]:
            final_winner = winner
            final_loser = (
                game_state.player1.user 
                if final_winner.id == game_state.player2.user.id 
                else game_state.player2.user
            )
        elif reason == GameEndReason.REVEAL:
            p1_score, p2_score = game_state.player1.score, game_state.player2.score
            if p1_score > p2_score:
                final_winner, final_loser = game_state.player1.user, game_state.player2.user
            elif p2_score > p1_score:
                final_winner, final_loser = game_state.player2.user, game_state.player1.user
            # If tie, no HP change
        
        # Update HP if there's a winner
        if final_winner and final_loser:
            winner_match_data = match_data.get_player_data(final_winner.id)
            loser_match_data = match_data.get_player_data(final_loser.id)
            
            bet_amount = match_data.current_bet
            winner_match_data.hp += bet_amount
            loser_match_data.hp -= bet_amount
            
            # Update game statistics
            stats_manager.update_game_result(final_winner.id, final_loser.id)
            stats_manager.save_stats()
        
        # Cleanup resources
        game_state.cleanup()
        
        # Update message with game results (show for 3 seconds)
        if game_state.public_message:
            try:
                embed = EmbedCreator.create_endgame_embed(
                    game_state, reason, winner, timed_out_player
                )
                await game_state.public_message.edit(embed=embed, view=None)
            except discord.NotFound:
                logger.warning(f"Public message not found for ended game")
            except Exception as e:
                logger.error(f"Error updating endgame embed: {e}")
        
        # Wait a moment to show results, then decide what to do next
        await asyncio.sleep(3)
        
        # Check for elimination
        if match_data.has_elimination:
            # ELIMINATION: Show match results with rematch options
            await MatchManager.handle_elimination(game_key)
        else:
            # NO ELIMINATION: Continue to next game automatically
            await MatchManager.start_next_game(game_key)
    
    @staticmethod
    async def create_timer_task(game_key: frozenset) -> asyncio.Task:
        """Create AFK timer task"""
        async def timer_task():
            try:
                await asyncio.sleep(config.GAME_TIMEOUT)
                if game_key in active_games:
                    game_state = active_games[game_key]
                    current_player = game_state.get_current_player()
                    opponent = game_state.get_opponent_data(current_player.user.id)
                    
                    await GameManager.end_game(
                        game_key, 
                        GameEndReason.TIMEOUT, 
                        winner=opponent.user, 
                        timed_out_player=current_player.user
                    )
                    logger.info(f"Game {game_key} ended due to timeout")
            except asyncio.CancelledError:
                pass
        
        return asyncio.create_task(timer_task())
    
    @staticmethod
    async def create_display_updater_task(game_key: frozenset) -> asyncio.Task:
        """Create display updater task"""
        async def updater_task():
            try:
                while game_key in active_games:
                    await asyncio.sleep(5)  # Update every 5 seconds
                    if game_key in active_games:
                        await GameManager.update_public_embed(game_key)
            except asyncio.CancelledError:
                pass
        
        return asyncio.create_task(updater_task())

# ==================== MATCH MANAGEMENT ====================

class MatchManager:
    """Match management system for HP battles"""
    
    @staticmethod
    async def start_new_game(match_key: frozenset) -> None:
        """Start a new game within existing match"""
        if match_key not in active_matches:
            return
            
        match_data = active_matches[match_key]
        player1, player2 = match_data.player1.user, match_data.player2.user
        channel = match_data.channel
        
        if not channel:
            logger.error(f"No channel reference for match {match_key}")
            return
        
        # Create new game state
        game_state = GameState(player1, player2, channel, match_data)
        active_games[match_key] = game_state
        
        # Send new game message
        embed = EmbedCreator.create_game_embed(game_state)
        view = GameView(match_key)
        
        try:
            message = await channel.send(
                f"🎮 **Game {match_data.game_number} Starting!** "
                f"Betting **{match_data.current_bet} HP**",
                embed=embed, view=view
            )
            game_state.public_message = message
        except Exception as e:
            logger.error(f"Error sending new game message: {e}")
            return
        
        # Create and track tasks
        timer_task = await GameManager.create_timer_task(match_key)
        updater_task = await GameManager.create_display_updater_task(match_key)
        game_state.add_task(timer_task)
        game_state.add_task(updater_task)
        
        # REMOVED: DM sending logic - cards will only be visible via "View Cards" button
        # Send reminder message instead
        await channel.send(
            f"🃏 {player1.mention} {player2.mention} - "
            f"Use the **🃏 View Cards** button to see your starting hands!",
            delete_after=10
        )
        
        logger.info(f"Started game {match_data.game_number} for match {match_key}")
    
    @staticmethod
    async def start_next_game(match_key: frozenset) -> None:
        """Start next game in sequence"""
        if match_key not in active_matches:
            return
            
        match_data = active_matches[match_key]
        match_data.game_number += 1
        
        # Send transition message
        if match_data.channel:
            try:
                await match_data.channel.send(
                    f"🔄 **Continuing Match!** Game {match_data.game_number} starting in 3 seconds...",
                    delete_after=3
                )
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"Error sending transition message: {e}")
        
        await MatchManager.start_new_game(match_key)
    
    @staticmethod
    async def handle_elimination(match_key: frozenset) -> None:
        """Handle player elimination and show rematch options"""
        if match_key not in active_matches:
            return
            
        match_data = active_matches[match_key]
        match_data.status = MatchStatus.ELIMINATION
        
        winner = match_data.winner
        eliminated = match_data.player1 if match_data.player1.is_eliminated else match_data.player2
        
        # Update match statistics
        stats_manager.update_match_result(winner.user.id, eliminated.user.id)
        stats_manager.save_stats()
        
        # Create elimination embed with rematch options
        embed = EmbedCreator.create_elimination_embed(match_data)
        view = RematchView(match_key)
        
        # Send elimination message in the channel
        if match_data.channel:
            try:
                await match_data.channel.send(
                    f"💀 **MATCH OVER!** {winner.user.mention} wins the HP battle!",
                    embed=embed, view=view
                )
            except Exception as e:
                logger.error(f"Error sending elimination message: {e}")
        
        logger.info(f"Match {match_key} ended with elimination. Winner: {winner.user}")
    
    @staticmethod
    async def end_match(match_key: frozenset) -> None:
        """Clean up and end match completely"""
        if match_key in active_matches:
            active_matches.pop(match_key)
        
        # Also clean up any active games for this match
        if match_key in active_games:
            game_state = active_games.pop(match_key)
            game_state.cleanup()
        
        logger.info(f"Match {match_key} completely ended and cleaned up")

# ==================== BOT SETUP ====================

# Bot configuration
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix=config.BOT_PREFIX, intents=intents, help_command=None)

# ==================== BOT EVENTS ====================

@bot.event
async def on_ready():
    """Bot ready event with improved logging"""
    logger.info(f'Bot logged in as {bot.user} (ID: {bot.user.id})')
    
    # Sync commands
    try:
        logger.info("Syncing slash commands...")
        synced = await bot.tree.sync()
        logger.info(f"Successfully synced {len(synced)} command(s)")
        
        # Debug output
        print("=== Synced Commands ===")
        for cmd in synced:
            print(f"  ✓ /{cmd.name}: {cmd.description}")
        print("=====================")
        
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")
    
    # Send startup message
    if config.CHANNEL_ID:
        try:
            channel = bot.get_channel(config.CHANNEL_ID)
            if channel:
                embed = EmbedCreator.create_help_embed()
                embed.add_field(
                    name="🚀 Bot Status", 
                    value="HP Battle System is online! Challenge opponents and fight until elimination!", 
                    inline=False
                )
                await channel.send(embed=embed)
                logger.info(f"Startup message sent to #{channel.name}")
        except Exception as e:
            logger.error(f"Failed to send startup message: {e}")

@bot.event
async def on_command_error(ctx, error):
    """Improved command error handling"""
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument: `{error.param.name}`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(f"❌ Could not find member. Make sure the mention is correct.")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("❌ I don't have the required permissions to run this command.")
    else:
        logger.error(f"Unexpected command error: {error}")
        await ctx.send("❌ An unexpected error occurred. Please try again.")

# ==================== SLASH COMMANDS ====================

@bot.tree.command(name="help", description="Show bot help and HP battle system")
async def help_slash(interaction: discord.Interaction):
    """Help command"""
    try:
        logger.info(f"Help command used by {interaction.user}")
        embed = EmbedCreator.create_help_embed()
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await handle_command_error(interaction, e, "help_slash")

@bot.tree.command(name="play", description="Start a new HP Battle match")
async def play_slash(interaction: discord.Interaction, opponent: discord.Member):
    """UPDATED: Start new HP battle match - NO DM VERSION"""
    try:
        logger.info(f"Play command: {interaction.user} vs {opponent}")
        
        player1, player2 = interaction.user, opponent
        
        # Validation
        if player1.bot or player2.bot:
            await interaction.response.send_message("❌ Cannot play against bots!", ephemeral=True)
            return
        if player1 == player2:
            await interaction.response.send_message("❌ You cannot challenge yourself!", ephemeral=True)
            return
        
        match_key = frozenset({player1.id, player2.id})
        if match_key in active_matches:
            await interaction.response.send_message(
                "❌ A match between these players is already in progress!", 
                ephemeral=True
            )
            return
        
        # Create match data with channel reference
        match_data = MatchData(
            MatchPlayerData(player1),
            MatchPlayerData(player2),
            channel=interaction.channel
        )
        active_matches[match_key] = match_data
        
        # Create first game
        game_state = GameState(player1, player2, interaction.channel, match_data)
        active_games[match_key] = game_state
        
        await interaction.response.send_message("🎲 Starting HP Battle...")
        game_state.public_message = await interaction.original_response()
        
        # Create and track tasks
        timer_task = await GameManager.create_timer_task(match_key)
        updater_task = await GameManager.create_display_updater_task(match_key)
        game_state.add_task(timer_task)
        game_state.add_task(updater_task)
        
        await GameManager.update_public_embed(match_key)
        
        # CHANGED: Send ephemeral hand to player1 (the one who started the match)
        try:
            p1_embed = EmbedCreator.create_private_hand_embed(game_state.player1)
            await interaction.followup.send(embed=p1_embed, ephemeral=True)
        except Exception as e:
            logger.error(f"Error sending ephemeral hand to player1: {e}")
        
        # CHANGED: Send public message reminder for both players
        await interaction.followup.send(
            f"🎮 {player1.mention} {player2.mention} - HP Battle has started! "
            f"Each player starts with **{config.STARTING_HP} HP**. "
            f"Click the **🃏 View Cards** button above to see your starting hands!",
            ephemeral=False
        )
        
        logger.info(f"HP Battle started between {player1} and {player2} - NO DM VERSION")
    except Exception as e:
        await handle_command_error(interaction, e, "play_slash")

@bot.tree.command(name="profil", description="View player's game and match statistics")
async def profile_slash(interaction: discord.Interaction, user: discord.Member):
    """View profile statistics command"""
    try:
        logger.info(f"Profile command used by {interaction.user} for {user.display_name}")
        
        user_stats = stats_manager.get_user_stats(user.id)
        embed = EmbedCreator.create_profile_embed(user, user_stats)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await handle_command_error(interaction, e, "profile_slash")

@bot.tree.command(name="stats", description="View your game and match statistics")
async def stats_slash(interaction: discord.Interaction):
    """View own statistics command"""
    try:
        logger.info(f"Stats command used by {interaction.user}")
        
        user_stats = stats_manager.get_user_stats(interaction.user.id)
        embed = EmbedCreator.create_profile_embed(interaction.user, user_stats)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await handle_command_error(interaction, e, "stats_slash")

@bot.tree.command(name="sync", description="Manually sync slash commands (owner only)")
async def sync_slash(interaction: discord.Interaction):
    """Manual sync command for owner"""
    try:
        if config.OWNER_ID and interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message(
                "❌ Only the bot owner can use this command!", 
                ephemeral=True
            )
            return
        
        await interaction.response.defer(ephemeral=True)
        synced = await bot.tree.sync()
        await interaction.followup.send(
            f"✅ Successfully synced {len(synced)} command(s)!", 
            ephemeral=True
        )
        logger.info(f"Manual sync completed: {len(synced)} commands")
    except Exception as e:
        try:
            await interaction.followup.send(f"❌ Sync failed: {e}", ephemeral=True)
        except:
            pass
        logger.error(f"Sync error: {e}")

# ==================== LEGACY PREFIX COMMANDS ====================

@bot.command(name='help', aliases=['menu'])
async def help_prefix(ctx):
    """Legacy prefix help command"""
    embed = EmbedCreator.create_help_embed()
    await ctx.send(embed=embed)

# ==================== UTILITY FUNCTIONS ====================

def get_game_by_player(player_id: int) -> Optional[Tuple[frozenset, GameState]]:
    """Get game containing specific player"""
    for game_key, game_state in active_games.items():
        if player_id in game_key:
            return game_key, game_state
    return None

def get_match_by_player(player_id: int) -> Optional[Tuple[frozenset, MatchData]]:
    """Get match containing specific player"""
    for match_key, match_data in active_matches.items():
        if player_id in match_key:
            return match_key, match_data
    return None

async def cleanup_all_games():
    """Clean up all active games and matches on shutdown"""
    logger.info("Cleaning up all active games and matches...")
    
    for game_state in active_games.values():
        game_state.cleanup()
    active_games.clear()
    
    active_matches.clear()
    
    # Save final stats
    stats_manager.save_stats()
    logger.info("All games and matches cleaned up")

# ==================== MAIN EXECUTION ====================

def validate_environment() -> bool:
    """Validate required environment variables"""
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        logger.error("DISCORD_TOKEN not found in environment variables!")
        logger.error("Create a .env file with: DISCORD_TOKEN=your_bot_token_here")
        return False
    return True

def main():
    """Main bot execution function"""
    try:
        if not validate_environment():
            return
        
        logger.info("Starting Twenty One Bot with HP Battle System...")
        logger.info(f"Configuration: Timeout={config.GAME_TIMEOUT}s, Max Card={config.MAX_CARD_VALUE}, Starting HP={config.STARTING_HP}")
        
        # Register cleanup on bot close
        @bot.event
        async def on_close():
            await cleanup_all_games()
        
        token = os.getenv('DISCORD_TOKEN')
        bot.run(token)
        
    except discord.LoginFailure:
        logger.error("LOGIN FAILED: Invalid bot token")
        logger.error("Please check your token in the .env file")
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Error running bot: {e}")
    finally:
        # Final cleanup
        logger.info("Bot shutdown complete")

if __name__ == "__main__":
    main()