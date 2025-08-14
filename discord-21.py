import discord
from discord.ext import commands
import random
import asyncio
import os

# Load environment variables with error handling
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("python-dotenv tidak terinstall. Install dengan: pip install python-dotenv")
    print("Atau gunakan environment variable sistem.")
except UnicodeDecodeError:
    print("Error membaca file .env. File mungkin corrupt atau encoding salah.")
    print("Hapus file .env dan buat ulang dengan encoding UTF-8.")
except Exception as e:
    print(f"Error loading .env file: {e}")
    print("Melanjutkan dengan environment variable sistem...")

# --- KONFIGURASI PENTING ---
# GANTI DENGAN ID CHANNEL TEMPAT ANDA INGIN BOT MENGIRIM PESAN SAAT DINYALAKAN

# GANTI DENGAN USER ID ANDA UNTUK COMMAND SYNC
  # Ganti dengan ID Discord Anda 

# Konfigurasi bot
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="#", intents=intents, help_command=None)

# --- MANAJEMEN STATE GAME ---
active_games = {}

# --- FUNGSI EMBED ---

def create_help_embed():
    """Membuat embed untuk pesan bantuan."""
    embed = discord.Embed(
        title="📜 Bantuan Bot Twenty One",
        description="Semua interaksi permainan dilakukan di server.",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Cara Bermain", value="Tujuanmu adalah mengumpulkan kartu dengan total nilai sedekat mungkin dengan 21 tanpa melewatinya. Kartu pertama dirahasiakan dari lawan.", inline=False)
    embed.add_field(name="#play twenty_one @Pemain1 challenge @Pemain2", value="Untuk memulai permainan baru di server (prefix command).", inline=False)
    embed.add_field(name="/play @opponent", value="Untuk memulai permainan baru di server (slash command - DIREKOMENDASIKAN).", inline=False)
    embed.add_field(name="/drink", value="Mengambil satu kartu tambahan. Pesan hanya terlihat oleh Anda.", inline=False)
    embed.add_field(name="/continue", value="Bertahan dengan kartumu dan mengakhiri giliran. Pesan hanya terlihat oleh Anda.", inline=False)
    embed.set_footer(text="Bot Twenty One | Selamat Bermain!")
    return embed

def create_private_hand_embed(player_data):
    """Membuat embed ephemeral untuk menunjukkan kartu pribadi pemain."""
    embed = discord.Embed(
        title="🃏 Kartu di Tangan Anda 🃏",
        description=f"Cards: `{'` `'.join(map(str, player_data['cards']))}`\n**Total: {sum(player_data['cards'])}**",
        color=discord.Color.green()
    )
    embed.set_footer(text="Pesan ini hanya bisa dilihat oleh Anda.")
    return embed

# --- EVENT ON_READY ---
@bot.event
async def on_ready():
    """Event yang dijalankan saat bot berhasil login."""
    print(f'Bot telah login sebagai {bot.user}')
    print(f'Bot ID: {bot.user.id}')
    print('Bot siap menerima perintah.')
    
    # Sync slash commands dengan debugging lebih detail
    try:
        print("Mencoba sync slash commands...")
        synced = await bot.tree.sync()
        print(f"✅ Berhasil sync {len(synced)} command(s)!")
        for cmd in synced:
            print(f"  - /{cmd.name}: {cmd.description}")
        
        # Tunggu sebentar lalu cek apakah commands tersedia
        await asyncio.sleep(2)
        print("Slash commands sudah siap digunakan!")
        
    except Exception as e:
        print(f"❌ GAGAL sync commands: {e}")
        import traceback
        traceback.print_exc()
    
    if CHANNEL_ID != 0:
        try:
            channel = bot.get_channel(CHANNEL_ID)
            if channel:
                embed = create_help_embed()
                embed.add_field(
                    name="🔧 Status Commands", 
                    value="Slash commands telah di-sync! Ketik `/` untuk melihat daftar commands.", 
                    inline=False
                )
                await channel.send(embed=embed)
                print(f"Pesan panduan telah dikirim ke channel: {channel.name}")
            else:
                print(f"ERROR: Channel dengan ID {CHANNEL_ID} tidak ditemukan.")
        except Exception as e:
            print(f"ERROR: Gagal mengirim pesan ke channel: {e}")

# --- FUNGSI HELPER GAME ---

async def update_public_embed(game_key):
    """Memperbarui pesan publik di server."""
    if game_key not in active_games: return
    game = active_games[game_key]
    p1, p2 = game['player1']['object'], game['player2']['object']
    current_player = p1 if game['current_turn_id'] == p1.id else p2
    
    embed = discord.Embed(title="⚔️ Twenty One Challenge ⚔️", description=f"{p1.mention} vs {p2.mention}", color=discord.Color.red())
    
    p1_cards_display = f"`[x]` `{'` `'.join(map(str, game['player1']['cards'][1:]))}`"
    p2_cards_display = f"`[x]` `{'` `'.join(map(str, game['player2']['cards'][1:]))}`"
    
    embed.add_field(name=f"{p1.display_name}'s cards", value=p1_cards_display, inline=True)
    embed.add_field(name=f"{p2.display_name}'s cards", value=p2_cards_display, inline=True)
    
    p1_status = "✅ Siap" if game['player1']['continued'] else "🤔 Berpikir"
    p2_status = "✅ Siap" if game['player2']['continued'] else "🤔 Berpikir"
    embed.add_field(name="Status", value=f"{p1.mention}: {p1_status}\n{p2.mention}: {p2_status}", inline=False)
    embed.set_footer(text=f"Giliran: {current_player.display_name} | Gunakan /drink atau /continue di channel ini!")
    await game['public_message'].edit(embed=embed)

async def end_game(game_key, reason="reveal", winner=None, timed_out_player=None):
    """Mengakhiri permainan dan membersihkan state."""
    if game_key not in active_games: return
    game = active_games.pop(game_key)
    
    if 'timer_task' in game: game['timer_task'].cancel()

    p1_data, p2_data = game['player1'], game['player2']
    p1, p2 = p1_data['object'], p2_data['object']
    p1_total, p2_total = sum(p1_data['cards']), sum(p2_data['cards'])
    
    embed = discord.Embed(title="🏁 Game Over! 🏁", color=discord.Color.gold())
    
    if reason == "bust":
        embed.description = f"🔥 {winner.mention} menang karena lawannya Bust!"
    elif reason == "timeout":
        embed.description = f"⌛ {timed_out_player.mention} tidak merespon. {winner.mention} menang!"
    elif reason == "deck_empty":
        embed.description = "Deck kartu habis! Hasilnya seri."
    else: # Reveal
        p1_score = p1_total if p1_total <= 21 else 0
        p2_score = p2_total if p2_total <= 21 else 0
        if p1_score > p2_score: winner = p1
        elif p2_score > p1_score: winner = p2
        else: winner = None
        embed.description = f"🏆 Pemenangnya adalah {winner.mention}!" if winner else "⚖️ Hasilnya Seri!"

    embed.add_field(name=f"{p1.display_name}'s Hand (Total: {p1_total})", value=f"`{'` `'.join(map(str, p1_data['cards']))}`", inline=False)
    embed.add_field(name=f"{p2.display_name}'s Hand (Total: {p2_total})", value=f"`{'` `'.join(map(str, p2_data['cards']))}`", inline=False)
    
    await game['public_message'].edit(embed=embed)

async def afk_timer(game_key):
    """Timer AFK."""
    await asyncio.sleep(60)
    if game_key in active_games:
        game = active_games[game_key]
        timed_out_player_id = game['current_turn_id']
        timed_out_player = game['player1']['object'] if timed_out_player_id == game['player1']['object'].id else game['player2']['object']
        winner = game['player2']['object'] if timed_out_player_id == game['player1']['object'].id else game['player1']['object']
        await end_game(game_key, reason="timeout", winner=winner, timed_out_player=timed_out_player)

# --- SLASH COMMANDS ---

@bot.tree.command(name="help", description="Menampilkan bantuan bot")
async def help_slash(interaction: discord.Interaction):
    print(f"Help command dipanggil oleh {interaction.user}")
    await interaction.response.send_message(embed=create_help_embed())

@bot.tree.command(name="play", description="Memulai permainan Twenty One")
async def play_slash(interaction: discord.Interaction, opponent: discord.Member):
    print(f"Play command dipanggil oleh {interaction.user} vs {opponent}")
    player1 = interaction.user
    player2 = opponent
    
    if player1.bot or player2.bot or player1 == player2:
        await interaction.response.send_message("Tantangan tidak valid. Anda tidak bisa menantang bot atau diri sendiri.", ephemeral=True)
        return

    game_key = frozenset({player1.id, player2.id})
    if game_key in active_games:
        await interaction.response.send_message("Permainan antara kedua pemain ini sudah berlangsung.", ephemeral=True)
        return

    deck = list(range(1, 12))
    random.shuffle(deck)

    game_state = {
        "player1": {"object": player1, "cards": [deck.pop(), deck.pop()], "continued": False},
        "player2": {"object": player2, "cards": [deck.pop(), deck.pop()], "continued": False},
        "channel": interaction.channel, "deck": deck, "current_turn_id": player1.id,
    }
    active_games[game_key] = game_state
    
    await interaction.response.send_message(embed=discord.Embed(title="Mempersiapkan permainan..."))
    game_state['public_message'] = await interaction.original_response()
    
    game_state['timer_task'] = asyncio.create_task(afk_timer(game_key))
    await update_public_embed(game_key)

@bot.tree.command(name="drink", description="Mengambil kartu tambahan")
async def drink_slash(interaction: discord.Interaction):
    print(f"Drink command dipanggil oleh {interaction.user}")
    player_id = interaction.user.id
    game_key = next((key for key in active_games if player_id in key), None)
    if not game_key: 
        await interaction.response.send_message("Anda tidak sedang dalam permainan.", ephemeral=True)
        return

    game = active_games[game_key]
    if player_id != game['current_turn_id']:
        await interaction.response.send_message("Bukan giliranmu untuk bermain!", ephemeral=True)
        return
    
    player_key = 'player1' if game['player1']['object'].id == player_id else 'player2'
    if game[player_key]['continued']:
        await interaction.response.send_message("Anda sudah memilih untuk bertahan, tidak bisa mengambil kartu lagi.", ephemeral=True)
        return

    game['timer_task'].cancel()
    
    if not game['deck']:
        await interaction.response.send_message("Dek kartu habis!", ephemeral=True)
        await end_game(game_key, reason="deck_empty")
        return

    new_card = game['deck'].pop()
    game[player_key]['cards'].append(new_card)
    
    p_data = game[player_key]
    op_data = game['player2' if player_key == 'player1' else 'player1']
    
    await interaction.response.send_message(embed=create_private_hand_embed(p_data), ephemeral=True)
    
    if sum(p_data['cards']) > 21:
        await end_game(game_key, reason="bust", winner=op_data['object'])
    else:
        game['timer_task'] = asyncio.create_task(afk_timer(game_key))
        await update_public_embed(game_key)

@bot.tree.command(name="continue", description="Bertahan dengan kartu saat ini")
async def continue_slash(interaction: discord.Interaction):
    print(f"Continue command dipanggil oleh {interaction.user}")
    player_id = interaction.user.id
    game_key = next((key for key in active_games if player_id in key), None)
    if not game_key: 
        await interaction.response.send_message("Anda tidak sedang dalam permainan.", ephemeral=True)
        return

    game = active_games[game_key]
    if player_id != game['current_turn_id']:
        await interaction.response.send_message("Bukan giliranmu untuk bermain!", ephemeral=True)
        return

    player_key = 'player1' if game['player1']['object'].id == player_id else 'player2'
    if game[player_key]['continued']:
        await interaction.response.send_message("Anda sudah memilih untuk bertahan.", ephemeral=True)
        return

    game['timer_task'].cancel()
    game[player_key]['continued'] = True
    
    await interaction.response.send_message("Anda memilih untuk bertahan. Giliran berpindah ke lawan.", ephemeral=True)

    if game['player1']['continued'] and game['player2']['continued']:
        await end_game(game_key, reason="reveal")
    else:
        game['current_turn_id'] = game['player2' if player_key == 'player1' else 'player1']['object'].id
        game['timer_task'] = asyncio.create_task(afk_timer(game_key))
        await update_public_embed(game_key)

# Tambahkan command untuk manual sync (untuk debugging)
@bot.tree.command(name="sync", description="Sync slash commands (owner only)")
async def sync_slash(interaction: discord.Interaction):
    if interaction.user.id != YOUR_USER_ID:  # Ganti dengan ID Discord Anda
        await interaction.response.send_message("Hanya owner yang bisa menggunakan command ini!", ephemeral=True)
        return
    
    try:
        # Respond first to avoid timeout
        await interaction.response.defer(ephemeral=True)
        synced = await bot.tree.sync()
        await interaction.followup.send(f"✅ Synced {len(synced)} command(s)!", ephemeral=True)
        print(f"Manual sync berhasil: {len(synced)} commands")
    except Exception as e:
        try:
            await interaction.followup.send(f"❌ Error syncing: {e}", ephemeral=True)
        except:
            print(f"Error syncing commands: {e}")

# --- LEGACY PREFIX COMMANDS (OPTIONAL) ---
@bot.command(aliases=['menu'])
async def help(ctx):
    await ctx.send(embed=create_help_embed())

@bot.group(invoke_without_command=True, case_insensitive=True)
async def play(ctx):
    await ctx.send("Format: `#play twenty_one @Pemain1 challenge @Pemain2` atau gunakan `/play @opponent`")

@play.command(name="twenty_one")
async def play_twenty_one(ctx, p1: discord.Member, challenge_text: str, p2: discord.Member):
    if challenge_text.lower() != 'challenge' or ctx.author != p1 or p1.bot or p2.bot or p1 == p2:
        await ctx.send("Tantangan tidak valid. Pastikan format benar, Anda tidak menantang bot atau diri sendiri.")
        return

    game_key = frozenset({p1.id, p2.id})
    if game_key in active_games:
        await ctx.send("Permainan antara kedua pemain ini sudah berlangsung.")
        return

    deck = list(range(1, 12))
    random.shuffle(deck)

    game_state = {
        "player1": {"object": p1, "cards": [deck.pop(), deck.pop()], "continued": False},
        "player2": {"object": p2, "cards": [deck.pop(), deck.pop()], "continued": False},
        "channel": ctx.channel, "deck": deck, "current_turn_id": p1.id,
    }
    active_games[game_key] = game_state
    
    game_state['public_message'] = await ctx.send(embed=discord.Embed(title="Mempersiapkan permainan..."))
    
    game_state['timer_task'] = asyncio.create_task(afk_timer(game_key))
    await update_public_embed(game_key)

# Hapus prefix command drink dan continue - gunakan slash commands saja

# Hapus prefix command continue - gunakan slash commands saja

# --- ERROR HANDLING ---
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound): return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Perintah tidak lengkap. Kurang argumen: `{error.param.name}`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send(f"Tidak dapat menemukan pemain: `{error.argument}`. Pastikan mention-nya benar.")
    else:
        print(f"Error tidak terduga: {error}")

# --- MENJALANKAN BOT ---
try:
    # Ambil token dari environment variable
    TOKEN = os.getenv('DISCORD_TOKEN')
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN tidak ditemukan di file .env!")
        print("Buat file .env dengan format:")
        print("DISCORD_TOKEN=your_bot_token_here")
        exit(1)
    
    bot.run(TOKEN)
except discord.errors.LoginFailure:
    print("LOGIN GAGAL: Token bot tidak valid.")
    print("Periksa kembali token di file .env")
except Exception as e:
    print(f"Terjadi error saat menjalankan bot: {e}")