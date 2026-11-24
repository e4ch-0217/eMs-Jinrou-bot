"""Discord 人狼Bot の入り口。ロビー・コマンド・ボイスの監視を担当します。"""
from __future__ import annotations

import contextlib
import logging
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

from game import Game, Mode
from roles import composition_text, composition_total, parse_composition
from session import Session
from views import OptionsView, TestPanelView, options_text, panel_text
from store import Store

load_dotenv()
log = logging.getLogger("jinro")

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID")


class JinroBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True        # 参加者のVC状態を見るため(Developer Portal でオンにする)
        intents.voice_states = True
        intents.message_content = True   # 密談の発言を天国に出すため(Developer Portal でオンにする)
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.games: dict[int, Session] = {}
        self.store = Store()

    async def setup_hook(self) -> None:
        if GUILD_ID:  # サーバーを指定すると、コマンドがすぐ反映される
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        log.info("ログインしました: %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        s = self.games.get(message.guild.id)
        if s is not None:
            await s.on_secret_message(message)

    async def on_voice_state_update(self, member: discord.Member, before, after) -> None:
        s = self.games.get(member.guild.id)
        if s is None:
            return
        was_in = bool(before.channel and before.channel.id == s.vc.id)
        now_in = bool(after.channel and after.channel.id == s.vc.id)
        if was_in != now_in:
            await s.on_voice_change(member.id, joined=now_in)


client = JinroBot()


class KickSelect(discord.ui.Select):
    """募集中に、主催者が参加者を外すための選択肢。"""

    def __init__(self, session: Session):
        options = [discord.SelectOption(label=p.name, value=str(uid))
                   for uid, p in session.game.players.items() if uid != session.game.host_id][:25]
        super().__init__(placeholder="外す人を選んでください", options=options, min_values=1, max_values=1)
        self.session = session

    async def callback(self, interaction: discord.Interaction):
        s = self.session
        if s.started or interaction.user.id != s.game.host_id:
            await interaction.response.edit_message(content="今はキックできません。", view=None)
            return
        uid = int(self.values[0])
        p = s.game.players.get(uid)
        if p is None:
            await interaction.response.edit_message(content="その人は、もういません。", view=None)
            return
        s.game.remove_player(uid)
        await interaction.response.edit_message(content=f"**{p.name}** さんを外しました。", view=None)
        if s.lobby_message:
            with contextlib.suppress(discord.HTTPException):
                await s.lobby_message.edit(embed=s.lobby_embed())
        with contextlib.suppress(discord.HTTPException):
            await s.channel.send(f"🥾 **{p.name}** さんを募集から外しました。")


class LobbyView(discord.ui.View):
    def __init__(self, session: Session):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="参加する", style=discord.ButtonStyle.success, emoji="✋")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.session
        if s.started:
            await interaction.response.send_message("すでに開始しています。", ephemeral=True)
        elif not s.game.add_player(interaction.user.id, interaction.user.display_name):
            await interaction.response.send_message("すでに参加しているか、満員です。", ephemeral=True)
        else:
            await interaction.response.edit_message(embed=s.lobby_embed(), view=self)

    @discord.ui.button(label="退出する", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.session
        if s.started:
            await interaction.response.send_message("すでに開始しています。", ephemeral=True)
        elif interaction.user.id == s.game.host_id:
            await interaction.response.send_message(
                "主催者は退出できません。中止するときは `/jinro stop` を使ってください。", ephemeral=True)
        else:
            s.game.remove_player(interaction.user.id)
            await interaction.response.edit_message(embed=s.lobby_embed(), view=self)

    @discord.ui.button(label="キック", style=discord.ButtonStyle.danger, emoji="🥾")
    async def kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.session
        if interaction.user.id != s.game.host_id:
            await interaction.response.send_message("キックできるのは主催者だけです。", ephemeral=True)
        elif s.started:
            await interaction.response.send_message("すでに開始しています。", ephemeral=True)
        elif len(s.game.players) <= 1:
            await interaction.response.send_message("外せる人がいません。", ephemeral=True)
        else:
            view = discord.ui.View(timeout=60)
            view.add_item(KickSelect(s))
            await interaction.response.send_message("外す人を選んでください(募集から外れます)。", view=view, ephemeral=True)

    @discord.ui.button(label="ゲーム開始", style=discord.ButtonStyle.primary, emoji="🎮")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.session
        if interaction.user.id != s.game.host_id:
            await interaction.response.send_message("開始できるのは主催者だけです。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await s.start_game(interaction)


jinro = app_commands.Group(name="jinro", description="人狼ゲームの操作", guild_only=True)


@jinro.command(name="create", description="参加者を募集します(主催者はゲーム用のVCに入ってから実行)")
async def create(interaction: discord.Interaction):
    await create_lobby(interaction)


wolf_group = app_commands.Group(name="wolf", description="ワードウルフ")


@wolf_group.command(name="create", description="ワードウルフの参加者を募集します(主催者はゲーム用のVCに入ってから実行)")
async def wolf_create(interaction: discord.Interaction):
    await create_lobby(interaction, wordwolf=True)


async def create_lobby(interaction: discord.Interaction, wordwolf: bool = False):
    if client.games.get(interaction.guild_id):
        await interaction.response.send_message(
            "このサーバーではすでにゲームの準備中か進行中です。`/jinro stop` で中止できます。", ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("通常のテキストチャンネルで実行してください。", ephemeral=True)
        return
    voice = getattr(interaction.user, "voice", None)
    if not voice or not voice.channel:
        await interaction.response.send_message(
            "先にゲームで使うVCに入ってから実行してください(VCは必須です)。", ephemeral=True)
        return

    game = Game(interaction.guild_id, interaction.channel_id, interaction.user.id)
    game.add_player(interaction.user.id, interaction.user.display_name)
    if wordwolf:
        game.mode_override = Mode.WORDWOLF
    session = Session(client, interaction.guild, interaction.channel, voice.channel, game,
                      client.store, interaction.user.display_name)
    client.games[interaction.guild_id] = session

    perms = interaction.guild.me.guild_permissions
    missing = [label for ok, label in (
        (perms.manage_channels, "チャンネルの管理(天国・人狼の部屋用)"),
        (perms.manage_roles, "ロールの管理(VCで参加者以外を話せなくするため)"),
        (perms.mute_members, "メンバーをミュート(自動ミュート用)")) if not ok]
    note = ("⚠️ Botに次の権限がないため、一部の機能が使えません: " + "、".join(missing)) if missing else None

    await interaction.response.send_message(content=note, embed=session.lobby_embed(), view=LobbyView(session))
    session.lobby_message = await interaction.original_response()


@jinro.command(name="roles", description="役職構成を手動で決めます(主催者のみ・開始前)")
@app_commands.describe(setting="例: 人狼2 占い師1 騎士1 市民3 (空欄で、人数に合わせた自動構成に戻す)")
async def roles_command(interaction: discord.Interaction, setting: str = ""):
    s = client.games.get(interaction.guild_id)
    if not s or s.started:
        await interaction.response.send_message("参加者募集中のゲームがありません。", ephemeral=True)
        return
    if interaction.user.id != s.game.host_id:
        await interaction.response.send_message("主催者だけが変更できます。", ephemeral=True)
        return
    if not setting.strip():
        s.game.custom_composition = None
        await interaction.response.send_message("役職構成を自動(人数に合わせる)に戻しました。")
    else:
        try:
            s.game.custom_composition = parse_composition(setting)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        await interaction.response.send_message(
            "役職構成を設定しました: " + composition_text(s.game.custom_composition)
            + f"\n合計 **{composition_total(s.game.custom_composition)}人**(今の参加人数は{len(s.game.players)}人)。"
            + "合計が参加人数と合わない場合は開始できません。")
    if s.lobby_message:
        with contextlib.suppress(discord.HTTPException):
            await s.lobby_message.edit(embed=s.lobby_embed())


@jinro.command(name="options", description="ゲームの設定を見る・変える(変えるのは主催者・募集中だけ)")
async def options_command(interaction: discord.Interaction):
    s = client.games.get(interaction.guild_id)
    if not s:
        await interaction.response.send_message("参加者募集中のゲームがありません。", ephemeral=True)
        return
    await interaction.response.send_message(
        options_text(s.game) + "\n\n見る・変えるを、下のボタンから選んでください。",
        view=OptionsView(s), ephemeral=True)


jinro_test = app_commands.Group(name="test", description="テスト用(サーバー管理者だけ)", guild_only=True)


@jinro_test.command(name="add", description="テスト用の参加者を追加します(募集中・サーバー管理者だけ)")
@app_commands.describe(name="名前(例: テストA)")
async def test_add(interaction: discord.Interaction, name: str):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("サーバー管理者だけが使えます。", ephemeral=True)
        return
    s = client.games.get(interaction.guild_id)
    if not s or s.started:
        await interaction.response.send_message("参加者募集中のゲームがありません。", ephemeral=True)
        return
    uid = min([0] + list(s.game.players.keys())) - 1      # 負のIDで、本物のユーザーと重ならない
    name = (name.strip() or "テスト")[:20]
    if not s.game.add_player(uid, name):
        await interaction.response.send_message("これ以上参加できません。", ephemeral=True)
        return
    await interaction.response.send_message(f"🧪 テスト用の参加者 **{name}** を追加しました。", ephemeral=True)
    if s.lobby_message:
        with contextlib.suppress(discord.HTTPException):
            await s.lobby_message.edit(embed=s.lobby_embed())


@jinro_test.command(name="as", description="テスト用: 今どの人として操作するかを選びます(サーバー管理者だけ)")
async def test_as(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("サーバー管理者だけが使えます。", ephemeral=True)
        return
    s = client.games.get(interaction.guild_id)
    if not s:
        await interaction.response.send_message("ゲームがありません。", ephemeral=True)
        return
    await interaction.response.send_message(panel_text(s, interaction.user.id),
                                            view=TestPanelView(s, interaction.user.id), ephemeral=True)


jinro.add_command(jinro_test)


@jinro.command(name="stop", description="ゲームを中止します(主催者・サーバー管理者のみ)")
async def stop(interaction: discord.Interaction):
    s = client.games.get(interaction.guild_id)
    if not s:
        await interaction.response.send_message("中止できるゲームがありません。", ephemeral=True)
        return
    if interaction.user.id != s.game.host_id and not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("主催者かサーバー管理者だけが中止できます。", ephemeral=True)
        return
    await interaction.response.send_message("ゲームを中止します。")
    await s.abort()


@jinro.command(name="mode", description="モードを決めます(主催者のみ・開始前)")
@app_commands.describe(choice="自動(人数で決める) / 簡略化人狼にする(何人でも簡略化人狼で遊ぶ)")
@app_commands.choices(choice=[
    app_commands.Choice(name="自動(人数で決める)", value="auto"),
    app_commands.Choice(name="簡略化人狼にする", value="one_night"),
    app_commands.Choice(name="ワンナイト人狼にする", value="note"),
    app_commands.Choice(name="ワードウルフにする", value="wordwolf"),
])
async def mode_command(interaction: discord.Interaction, choice: app_commands.Choice[str]):
    s = client.games.get(interaction.guild_id)
    if not s or s.started:
        await interaction.response.send_message("参加者募集中のゲームがありません。", ephemeral=True)
        return
    if interaction.user.id != s.game.host_id:
        await interaction.response.send_message("主催者だけが変更できます。", ephemeral=True)
        return
    if choice.value == "wordwolf":
        s.game.mode_override = Mode.WORDWOLF
        text = "モードを**ワードウルフ**にしました(お題が違う人を当てるゲームです)。"
    elif choice.value == "note":
        s.game.mode_override = Mode.NOTE
        text = "モードを**ワンナイト人狼**にしました(noteのルールで遊びます)。\nルール: https://note.com/1nwerewolf/n/n446a1fde2286"
    elif choice.value == "one_night":
        s.game.mode_override = Mode.ONE_NIGHT
        text = "モードを**簡略化人狼**にしました(人数に関係なく、簡略化人狼で遊びます)。"
    else:
        s.game.mode_override = None
        text = "モードを**自動**(人数で決める)に戻しました。"
    s.game.custom_composition = None   # ワンナイトの役職は、自動の構成で作り直す
    await interaction.response.send_message(text)
    if s.lobby_message:
        with contextlib.suppress(discord.HTTPException):
            await s.lobby_message.edit(embed=s.lobby_embed())


@client.tree.command(name="pt", description="自分の残高(ポイント)を表示します")
@app_commands.guild_only()
async def pt_command(interaction: discord.Interaction):
    bal = client.store.balance(interaction.guild_id, interaction.user.id)
    await interaction.response.send_message(f"💰 残高: **{bal}pt**", ephemeral=True)


season = app_commands.Group(name="season", description="シーズン(ポイントのリセット)", guild_only=True)


class SeasonConfirmView(discord.ui.View):
    """記録を残さないリセットの、最後の確認。"""

    def __init__(self, guild_id: int):
        super().__init__(timeout=60)
        self.guild_id = guild_id

    @discord.ui.button(label="はい、記録を残さずにリセット", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        n = client.store.reset_season(self.guild_id, record=False)
        await interaction.response.edit_message(
            content=f"✅ リセットしました(記録は残していません)。対象: {n}人 → 2000pt", view=None)

    @discord.ui.button(label="やめる", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="リセットをやめました。", view=None)


@season.command(name="reset", description="全員のポイントを2000ptに戻します(サーバー管理者のみ)")
@app_commands.describe(record="リセット前のランキングを残すか")
@app_commands.choices(record=[
    app_commands.Choice(name="記録を残す", value="yes"),
    app_commands.Choice(name="記録を残さない(テスト用)", value="no"),
])
async def season_reset(interaction: discord.Interaction, record: app_commands.Choice[str]):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("サーバー管理者だけが実行できます。", ephemeral=True)
        return
    if client.games.get(interaction.guild_id):
        await interaction.response.send_message(
            "ゲームの準備中・進行中は、リセットできません。終わってから実行してください。", ephemeral=True)
        return
    if record.value == "no":
        await interaction.response.send_message(
            "⚠️ **記録を残さずに**リセットします。今のランキングは消えて、元に戻せません。本当によいですか?",
            view=SeasonConfirmView(interaction.guild_id), ephemeral=True)
        return
    n = client.store.reset_season(interaction.guild_id, record=True)
    await interaction.response.send_message(f"✅ リセットしました(記録を残しました)。対象: {n}人 → 2000pt")


client.tree.add_command(season)
client.tree.add_command(jinro)
client.tree.add_command(wolf_group)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN が設定されていません。.env ファイルを確認してください。")
    client.run(TOKEN)
