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

# ==================== STATISTICS MANAGER ====================

class StatsManager:
    """Efficient statistics management with caching"""
    
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
        return self._cache.get(str(user_id), {'wins': 0, 'losses': 0})
    
    def update_game_result(self, winner_id: int, loser_id: int) -> None:
        """Update game statistics"""
        winner_key, loser_key = str(winner_id), str(loser_id)
        
        # Initialize if not exists
        if winner_key not in self._cache:
            self._cache[winner_key] = {'wins': 0, 'losses': 0}
        if loser_key not in self._cache:
            self._cache[loser_key] = {'wins': 0, 'losses': 0}
        
        # Update stats
        self._cache[winner_key]['wins'] += 1
        self._cache[loser_key]['losses'] += 1
        self._cache_dirty = True
        
        logger.info(f"Stats updated: Winner {winner_id}, Loser {loser_id}")

# Global stats manager
stats_manager = StatsManager(config.STATS_FILE)

# ==================== GAME STATE MANAGEMENT ====================

class GameState:
    """Enhanced game state with better resource management"""
    
    def __init__(self, player1: discord.Member, player2: discord.Member, channel: discord.TextChannel):
        self.player1 = PlayerData(player1, [])
        self.player2 = PlayerData(player2, [])
        self.channel = channel
        
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
        self.reset_turn_timer()  # Always reset to full time on turn switch
    
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

# Game storage
active_games: Dict[frozenset, GameState] = {}

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
    """Centralized embed creation with consistent styling"""
    
    @staticmethod
    def create_help_embed() -> discord.Embed:
        """Create help embed"""
        embed = discord.Embed(
            title="🎲 Twenty One Bot - Help",
            description="A card game where you try to get as close to 21 as possible without going over!",
            color=discord.Color.blue()
        )
        
        fields = [
            ("🎯 How to Play", 
             "Get cards totaling as close to 21 as possible without going over. "
             "You start with 1 hidden card, then take more cards that become visible to opponents.", False),
            ("🚀 Start Game", "`/play @opponent` - Challenge another player", False),
            ("🎴 Game Controls", 
             "**🃏 View Cards Button** - Click to see your current hand\n"
             "`/drink` - Take another card\n"
             "`/continue` - Keep current cards and end turn", False),
            ("❓ Other Commands", 
             "`/help` - Show this help message\n"
             "`/profil @user` - View player statistics", False)
        ]
        
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
        
        embed.set_footer(text="All game interactions are private to you!")
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
        """Create public game embed"""
        p1, p2 = game_state.player1, game_state.player2
        current_player = game_state.get_current_player()
        
        embed = discord.Embed(
            title="🎲 Twenty One Battle",
            description=f"⚔️ {p1.user.mention} **VS** {p2.user.mention}",
            color=discord.Color.gold()
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
        
        embed.add_field(
            name="💡 How to Play",
            value="🃏 **Click 'View Cards' button** to see your hand\n"
                  "📱 Use `/drink` to take a card or `/continue` to pass\n"
                  "🎯 First card stays hidden, additional cards are visible",
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
        """Create endgame results embed"""
        p1, p2 = game_state.player1, game_state.player2
        
        embed = discord.Embed(title="🏁 Game Over!", color=discord.Color.gold())
        
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
        
        return embed
    
    @staticmethod
    def create_profile_embed(user: discord.Member, user_stats: Dict[str, int]) -> discord.Embed:
        """Create profile statistics embed"""
        embed = discord.Embed(
            title=f"📊 Game Profile - {user.display_name}",
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        
        if not any(user_stats.values()):
            embed.description = "This player hasn't played any games yet."
        else:
            wins = user_stats.get('wins', 0)
            losses = user_stats.get('losses', 0)
            total_games = wins + losses
            win_rate = (wins / total_games * 100) if total_games > 0 else 0
            
            embed.add_field(name="🏆 Wins", value=f"**{wins}**", inline=True)
            embed.add_field(name="💔 Losses", value=f"**{losses}**", inline=True)
            embed.add_field(name="📈 Win Rate", value=f"**{win_rate:.1f}%**", inline=True)
            embed.add_field(name="⚔️ Total Games", value=f"**{total_games}**", inline=False)
        
        return embed

# ==================== UI COMPONENTS ====================

class GameView(discord.ui.View):
    """Enhanced view with better error handling"""
    
    def __init__(self, game_key: frozenset):
        super().__init__(timeout=None)
        self.game_key = game_key
    
    @discord.ui.button(label="🃏 View Cards", style=discord.ButtonStyle.primary, custom_id="view_cards")
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

# ==================== GAME MANAGEMENT ====================

class GameManager:
    """Centralized game management with better resource handling"""
    
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
        """End game with proper cleanup and stats update"""
        if game_key not in active_games:
            return
        
        game_state = active_games.pop(game_key)
        
        # Determine final winner/loser for stats
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
        
        # Update stats
        if final_winner and final_loser:
            stats_manager.update_game_result(final_winner.id, final_loser.id)
            stats_manager.save_stats()
        
        # Cleanup resources
        game_state.cleanup()
        
        # Update message
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
                    value="Bot is online and slash commands are ready!\nType `/` to see available commands.", 
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

@bot.tree.command(name="help", description="Show bot help and commands")
async def help_slash(interaction: discord.Interaction):
    """Help command"""
    try:
        logger.info(f"Help command used by {interaction.user}")
        embed = EmbedCreator.create_help_embed()
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await handle_command_error(interaction, e, "help_slash")

@bot.tree.command(name="play", description="Start a new Twenty One game")
async def play_slash(interaction: discord.Interaction, opponent: discord.Member):
    """Start new game command"""
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
        
        game_key = frozenset({player1.id, player2.id})
        if game_key in active_games:
            await interaction.response.send_message(
                "❌ A game between these players is already in progress!", 
                ephemeral=True
            )
            return
        
        # Create game
        game_state = GameState(player1, player2, interaction.channel)
        active_games[game_key] = game_state
        
        await interaction.response.send_message("🎲 Setting up the game...")
        game_state.public_message = await interaction.original_response()
        
        # Create and track tasks
        timer_task = await GameManager.create_timer_task(game_key)
        updater_task = await GameManager.create_display_updater_task(game_key)
        game_state.add_task(timer_task)
        game_state.add_task(updater_task)
        
        await GameManager.update_public_embed(game_key)
        
        # Send initial hands
        p1_embed = EmbedCreator.create_private_hand_embed(game_state.player1)
        await interaction.followup.send(embed=p1_embed, ephemeral=True)
        
        await interaction.followup.send(
            f"{player2.mention}, click the **🃏 View Cards** button to see your starting hand!",
            ephemeral=True
        )
        
        logger.info(f"Game started between {player1} and {player2}")
    except Exception as e:
        await handle_command_error(interaction, e, "play_slash")

@bot.tree.command(name="drink", description="Take another card")
async def drink_slash(interaction: discord.Interaction):
    """Take card command"""
    try:
        logger.info(f"Drink command used by {interaction.user}")
        
        player_id = interaction.user.id
        game_key = next((key for key in active_games if player_id in key), None)
        
        if not game_key:
            await interaction.response.send_message("❌ You're not in an active game!", ephemeral=True)
            return
        
        game_state = active_games[game_key]
        
        # Validation
        if player_id != game_state.current_turn_id:
            await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
            return
        
        player_data = game_state.get_player_data(player_id)
        if player_data.continued:
            await interaction.response.send_message(
                "❌ You already chose to continue! Cannot take more cards.", 
                ephemeral=True
            )
            return
        
        if not game_state.deck:
            await interaction.response.send_message("❌ The deck is empty!", ephemeral=True)
            await GameManager.end_game(game_key, GameEndReason.DECK_EMPTY)
            return
        
        # Cancel current tasks
        game_state.cleanup()
        
        # Deal card
        new_card = game_state.deck.pop()
        player_data.cards.append(new_card)
        
        # IMPORTANT: Reset timer for same player after drinking
        game_state.reset_turn_timer()
        
        # Send updated hand
        embed = EmbedCreator.create_private_hand_embed(player_data)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Check for bust
        if player_data.is_bust:
            opponent_data = game_state.get_opponent_data(player_id)
            await GameManager.end_game(game_key, GameEndReason.BUST, winner=opponent_data.user)
            logger.info(f"{interaction.user} went bust with {player_data.total}")
        else:
            # Create new tasks with fresh timer
            timer_task = await GameManager.create_timer_task(game_key)
            updater_task = await GameManager.create_display_updater_task(game_key)
            game_state.add_task(timer_task)
            game_state.add_task(updater_task)
            
            # Update display immediately to show fresh timer
            await GameManager.update_public_embed(game_key)
    except Exception as e:
        await handle_command_error(interaction, e, "drink_slash")

@bot.tree.command(name="continue", description="Keep current cards and end your turn")
async def continue_slash(interaction: discord.Interaction):
    """Continue with current cards command"""
    try:
        logger.info(f"Continue command used by {interaction.user}")
        
        player_id = interaction.user.id
        game_key = next((key for key in active_games if player_id in key), None)
        
        if not game_key:
            await interaction.response.send_message("❌ You're not in an active game!", ephemeral=True)
            return
        
        game_state = active_games[game_key]
        
        # Validation
        if player_id != game_state.current_turn_id:
            await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
            return
        
        player_data = game_state.get_player_data(player_id)
        if player_data.continued:
            await interaction.response.send_message("❌ You already chose to continue!", ephemeral=True)
            return
        
        # Cancel current tasks
        game_state.cleanup()
        
        # Set continued status
        player_data.continued = True
        await interaction.response.send_message(
            "✅ You chose to continue with your current cards. Turn passes to opponent.", 
            ephemeral=True
        )
        
        # Check if both players continued
        if game_state.both_continued:
            await GameManager.end_game(game_key, GameEndReason.REVEAL)
            logger.info(f"Game {game_key} ended - both players continued")
        else:
            # Switch turn (this will reset timer automatically)
            game_state.switch_turn()
            
            # Create new tasks with fresh timer
            timer_task = await GameManager.create_timer_task(game_key)
            updater_task = await GameManager.create_display_updater_task(game_key)
            game_state.add_task(timer_task)
            game_state.add_task(updater_task)
            
            # Update display immediately to show fresh timer
            await GameManager.update_public_embed(game_key)
    except Exception as e:
        await handle_command_error(interaction, e, "continue_slash")

@bot.tree.command(name="profil", description="View player's game statistics")
async def profile_slash(interaction: discord.Interaction, user: discord.Member):
    """View profile statistics command"""
    try:
        logger.info(f"Profile command used by {interaction.user} for {user.display_name}")
        
        user_stats = stats_manager.get_user_stats(user.id)
        embed = EmbedCreator.create_profile_embed(user, user_stats)
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await handle_command_error(interaction, e, "profile_slash")

@bot.tree.command(name="stats", description="View your game statistics")
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

async def cleanup_all_games():
    """Clean up all active games on shutdown"""
    logger.info("Cleaning up all active games...")
    for game_state in active_games.values():
        game_state.cleanup()
    active_games.clear()
    
    # Save final stats
    stats_manager.save_stats()
    logger.info("All games cleaned up")

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
        
        logger.info("Starting Twenty One Bot...")
        logger.info(f"Configuration: Timeout={config.GAME_TIMEOUT}s, Max Card={config.MAX_CARD_VALUE}")
        
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