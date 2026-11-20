"""1ゲーム分の進行(Discord側)。ルールは game.py、ポイントは points.py / store.py、ボタンの受け口は views.py / handlers.py。"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import random

import discord

import config as C
import points
from game import MIN_PLAYERS, RESULT_LABEL, Game, Mode, Phase
from handlers import Handlers
import note_rules as NR
from roles import ROLES, SURVEY_ROLES
from collections import Counter
from views import (COView, CloseNowView, SpectateView, ConfirmVoteView, DayView, GameView, HopeView, JumpView, NightView,
                   NoteVoteView, SpeakEndView, TieVoteView)

log = logging.getLogger("jinro")


def ceil_to_30(seconds: int) -> int:
    return int(math.ceil(seconds / 30) * 30)


async def wait_event(event: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(event.wait(), seconds)
        return True
    except asyncio.TimeoutError:
        return False


class Session(Handlers):
    def __init__(self, bot, guild: discord.Guild, channel: discord.TextChannel,
                 vc: discord.VoiceChannel, game: Game, store, host_name: str):
        self.bot, self.guild, self.channel, self.vc = bot, guild, channel, vc
        self.origin = channel                      # 募集をしたチャンネル(ロビーはここ)
        self.game_channel: discord.TextChannel | None = None   # 進行専用のチャンネル
        self.game, self.store, self.host_name = game, store, host_name

        self.payers: set[int] = set()
        self.hopes: dict[int, tuple[str | None, str | None]] = {}
        self.lobby_message: discord.Message | None = None
        self.co_message: discord.Message | None = None
        self.heaven: discord.TextChannel | None = None
        self.wolf_channel: discord.TextChannel | None = None
        self.task: asyncio.Task | None = None
        self.started = self.finished = self.cleaned = False
        self.spectate_msg: discord.Message | None = None  # 観戦ボタンのメッセージ(終わったら消す)
        self.close_now = asyncio.Event()           # 主催者が「今すぐ閉じる」を押した
        self.ending_early = False                   # 即終了・人狼全滅での終了中(中止の表示を出さない)

        self.voice_mode: tuple[str, int | None] = ("day", None)   # 今のミュート状態
        self.note_choices: dict[int, int | None] = {}              # ワンナイト人狼の夜の選択
        self.note_votes: dict[int, int] = {}
        self.note_vote_done = asyncio.Event()
        # VCから抜けた人 -> 抜けた時点の生存夜数(次の朝の発表までに戻らなければ突然死)
        self.absent: dict[int, int] = {}
        self.roles_ready = False        # 役職が配られたあと(それまでのVCの出入りは、配布後にまとめて見る)

        # 夜
        self.pending_night: set[int] = set()
        self.night_event = asyncio.Event()
        self.night_replies: dict[int, tuple[discord.Interaction, str]] = {}  # 夜の終わりに書き換える表示
        self.last_night = None
        self.first_night_info: dict[int, str] = {}
        self.medium_text: str | None = None

        # 昼・投票
        self.skip_votes: set[int] = set()
        self.skip_event = asyncio.Event()
        self.ext_votes: set[int] = set()
        self.ext_used = 0
        self.ext_pending = False
        self.jump_queue: list[int] = []
        self.votes: dict[int, int] = {}
        self.tie_candidates: list[int] = []
        self.speak_end = asyncio.Event()

    # ================================================================
    # 補助
    # ================================================================
    def is_player(self, uid: int) -> bool:
        return uid in self.game.players

    def member(self, uid: int) -> discord.Member | None:
        return self.guild.get_member(uid)

    def in_vc(self, m: discord.Member | None) -> bool:
        return bool(m and m.voice and m.voice.channel and m.voice.channel.id == self.vc.id)

    async def set_mute(self, uid: int, muted: bool) -> None:
        m = self.member(uid)
        if self.in_vc(m):
            with contextlib.suppress(discord.HTTPException):
                await m.edit(mute=muted)

    def _should_mute(self, uid: int) -> bool:
        mode, speaker = self.voice_mode
        p = self.game.players[uid]
        if mode in ("night", "silent"):
            return True
        if mode == "speaker":
            return uid != speaker
        return not p.alive

    async def apply_voice(self, mode: str, speaker: int | None = None) -> None:
        """mode: "night"(全員ミュート) / "day"(生存者は話せる) / "speaker"(speakerだけ話せる)。"""
        self.voice_mode = (mode, speaker)
        await asyncio.gather(*(self.set_mute(p.user_id, self._should_mute(p.user_id))
                               for p in self.game.players.values() if not p.quit_early))

    async def speak(self, uid: int, seconds: int, after: str = "day",
                    prompt: str = "遺言があればどうぞ", text: str | None = None) -> None:
        """uid だけが話せる時間。「意見終了」か制限時間で終わる。終わったあとは after のモードに戻す。"""
        self.speak_end.clear()
        message = text or f"🎤 <@{uid}> さん、{prompt}(最大{seconds}秒)。終わったら「意見終了」を押してください。"
        await self.channel.send(message, view=SpeakEndView(self, uid))   # 案内を先に出す(ミュートの切り替えを待たせない)
        await self.apply_voice("speaker", uid)
        await wait_event(self.speak_end, seconds)
        if after is not None:
            await self.apply_voice(after)

    async def lock_vc(self) -> None:
        """VCは参加者以外は話せない。"""
        with contextlib.suppress(discord.HTTPException):
            await self.vc.set_permissions(self.guild.default_role, speak=False, connect=True)
            for uid in self.game.players:
                if self.member(uid):
                    await self.vc.set_permissions(self.member(uid), speak=True, connect=True)

    async def unlock_vc(self) -> None:
        with contextlib.suppress(discord.HTTPException):
            await self.vc.set_permissions(self.guild.default_role, overwrite=None)
            for uid in self.game.players:
                if self.member(uid):
                    await self.vc.set_permissions(self.member(uid), overwrite=None)

    async def set_wolf_chat(self, open_: bool) -> None:
        """人狼の相談部屋は、夜の間だけ書き込める。"""
        if not self.wolf_channel:
            return
        with contextlib.suppress(discord.HTTPException):
            for p in self.game.players.values():
                if p.role == "人狼" and self.member(p.user_id):
                    await self.wolf_channel.set_permissions(
                        self.member(p.user_id), view_channel=True, send_messages=open_)

    async def kill(self, uid: int) -> None:
        g = self.game
        g.kill(uid)
        await self.set_mute(uid, True)
        m = self.member(uid)
        if m:
            with contextlib.suppress(discord.HTTPException):
                await self.vc.set_permissions(m, speak=False, connect=True)
                await self.channel.set_permissions(m, send_messages=False)
                if self.heaven:
                    await self.heaven.set_permissions(m, view_channel=True, send_messages=True)
        await self.to_heaven(f"💀 **{g.players[uid].name}** が天国にやってきました。")

    async def to_heaven(self, text: str) -> None:
        if self.heaven:
            with contextlib.suppress(discord.HTTPException):
                await self.heaven.send(text)

    def majority(self) -> int:
        return len(self.game.alive_players()) // 2 + 1

    # ================================================================
    # ロビーと開始
    # ================================================================
    def fee_now(self) -> int:
        return points.fee_for(self.game.preview_mode() in (Mode.ONE_NIGHT, Mode.NOTE))

    def lobby_embed(self) -> discord.Embed:
        g = self.game
        n = len(g.players)
        embed = discord.Embed(title="🐺 人狼 参加者募集中", color=discord.Color.dark_red())
        fee = self.fee_now()
        names = "\n".join(
            f"・{p.name}" + ("(免除)" if self.store.balance(self.guild.id, p.user_id) < fee else "")
            for p in g.players.values()) or "(まだいません)"
        embed.add_field(name=f"参加者 ({n}/20)", value=names, inline=False)
        if n >= MIN_PLAYERS:
            text = g.composition_label()
            if g.composition_error():
                text += f"\n⚠️ {g.composition_error()}"
            embed.add_field(name=f"モード: {g.preview_mode().value}", value=text, inline=False)
        else:
            embed.add_field(name="モード", value=f"{MIN_PLAYERS}人以上で開始できます。", inline=False)
        embed.add_field(
            name="参加費・VC",
            value=f"参加費 {fee}pt(残高が足りない人は免除)\nゲームVC: {self.vc.mention}(全員が入っている必要があります)",
            inline=False)
        embed.set_footer(text=f"主催者: {self.host_name}")
        return embed

    async def start_game(self, interaction: discord.Interaction) -> None:
        g = self.game
        if self.started:
            return
        absent = [p.name for p in g.players.values() if not self.in_vc(self.member(p.user_id))]
        if absent:
            await interaction.followup.send(
                "⚠️ 次の人がゲームVCに入っていません: " + "、".join(absent), ephemeral=True)
            return
        err = g.begin()
        if err:
            await interaction.followup.send(f"⚠️ {err}", ephemeral=True)
            return
        self.started = True
        fee = points.fee_for(g.one_night)
        self.payers = {uid for uid in g.players if self.store.balance(self.guild.id, uid) >= fee}
        for uid in self.payers:
            self.store.add(self.guild.id, uid, -fee)
        if self.lobby_message:
            with contextlib.suppress(discord.HTTPException):
                await self.lobby_message.edit(view=None)
        self.task = asyncio.create_task(self.run())
        await interaction.followup.send("ゲームを開始します!", ephemeral=True)

    async def hope_phase(self) -> None:
        await self.channel.send(embed=discord.Embed(
            title="🎲 希望役職",
            description=(f"「希望を出す」から、第1・第2希望を選んでください({C.HOPE_SECONDS}秒)。\n"
                         "希望は公開されません。重なったときは抽選です。"),
            color=discord.Color.gold()), view=HopeView(self))
        await asyncio.sleep(C.HOPE_SECONDS)
        self.game.assign_roles(self.hopes)
        await self.channel.send("🎲 役職を配りました。「役職を確認」から、自分の役職を見てください。")

    async def setup_game_channel(self) -> None:
        """進行(アナウンス・投票・COなど)を流す専用チャンネル。終わったら、天国と一緒に消す。"""
        origin = self.origin
        overwrites = dict(origin.overwrites)
        overwrites[self.guild.default_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=False)     # 見るのは全員、書くのはボットだけ
        overwrites[self.guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        try:
            self.game_channel = await self.guild.create_text_channel(
                "人狼-進行", category=origin.category, overwrites=overwrites,
                topic="ゲームの進行(自動で流れます)")
            self.channel = self.game_channel
            mentions = " ".join(f"<@{uid}>" for uid in self.game.players)
            await origin.send(f"{mentions}\n🐺 ゲームの進行は {self.game_channel.mention} で行います。そちらを見てください。",
                              )
        except discord.HTTPException:
            self.channel = origin
            await origin.send("⚠️ 進行用のチャンネルを作れませんでした。このチャンネルで進めます。")

    async def setup_channels(self) -> None:
        g = self.game
        hidden = {
            self.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            self.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        try:
            self.heaven = await self.guild.create_text_channel(
                "天国", category=self.channel.category, overwrites=dict(hidden),
                topic="死んだ人と観戦者だけが見られるチャンネル")
            wolves = [p for p in g.players.values() if p.role == "人狼"]
            if len(wolves) >= 2:
                overwrites = dict(hidden)
                for w in wolves:
                    if self.member(w.user_id):
                        overwrites[self.member(w.user_id)] = discord.PermissionOverwrite(
                            view_channel=True, send_messages=False)
                self.wolf_channel = await self.guild.create_text_channel(
                    "人狼の巣", category=self.channel.category, overwrites=overwrites,
                    topic="人狼だけの相談部屋(夜だけ書き込めます)")
        except discord.HTTPException:
            await self.channel.send("⚠️ チャンネルを作れませんでした。天国と人狼の部屋なしで進めます。")
        if self.heaven:
            if g.mode == Mode.NOTE:   # ワンナイト人狼: 役職は、処刑されたときとゲーム終了時だけ公開する
                await self.to_heaven("👀 **天国へようこそ。** 役職は、処刑されたときとゲーム終了時に公開されます。")
            else:
                await self.to_heaven("👀 **天国へようこそ。** 全員の役職です。\n" + g.role_summary())
            with contextlib.suppress(discord.HTTPException):   # 天国ができてから、観戦ボタンを出す
                self.spectate_msg = await self.origin.send(
                    "👀 天国ができました。観戦する人は、下のボタンを押してください。",
                    view=SpectateView(self))

    # ================================================================
    # 全体の流れ
    # ================================================================
    async def run(self) -> None:
        g = self.game
        try:
            await self.setup_game_channel()
            if g.mode == Mode.NOTE:
                g.assign_note_roles()                       # ワンナイト人狼: 希望役職はなし
            else:
                await self.hope_phase()
            self.roles_ready = True
            await self.setup_channels()
            await self.lock_vc()
            await self.channel.send(embed=self.game_embed(), view=GameView(self))
            await self.sweep_absent()
            if self.finished:
                return
            if g.mode == Mode.NOTE:
                g.day = 1
                await self.note_night()
                await self.discussion()
                winner = await self.note_vote()
            elif g.one_night:
                g.day = 1
                await self.night()
                await self.morning()                         # 襲撃された人の死亡とアナウンス
                if self.finished:
                    return                                   # 夜明けの突然死で、もう終わっている
                executed = await self.day_and_vote()
                winner = g.one_night_winner(executed)
            else:
                g.day = 1
                await self.first_night()
                winner = None
                while winner is None:
                    await self.day_and_vote()
                    winner = g.check_winner()
                    if winner:
                        break
                    await self.night()
                    await self.morning()
                    if self.finished:
                        return                               # 夜明けの突然死で、もう終わっている
                    winner = g.check_winner()
                    if winner:
                        break
                    g.day += 1
            await self.finish(winner)
        except asyncio.CancelledError:
            if self.ending_early:
                raise
            with contextlib.suppress(discord.HTTPException):
                await self.channel.send("🛑 ゲームを中止しました。参加費は返金されます。")
            self.refund()
            raise
        except Exception:
            log.exception("ゲーム進行中にエラーが発生しました")
            with contextlib.suppress(discord.HTTPException):
                await self.channel.send("⚠️ エラーが発生したため、ゲームを中止しました。参加費は返金されます。")
            self.refund()
        finally:
            await self.cleanup()

    def game_embed(self) -> discord.Embed:
        g = self.game
        seats = " → ".join(g.players[u].name for u in g.seats)
        return discord.Embed(
            title=f"🎮 {g.mode.value} 開始!",
            description=(f"参加者: {len(g.players)}人\n役職構成: {g.composition_label()}\n"
                         f"席順: {seats}\n\n"
                         "「役職を確認」: 自分にだけ見えます\n"
                         "「観戦する」: 死んだ人と観戦者が入れる天国\n"
                         "夜は「夜の行動」から選んでください。"),
            color=discord.Color.dark_red())

    # ---------- 夜 ----------
    async def first_night(self) -> None:
        g = self.game
        g.phase = Phase.NIGHT
        g.reset_night()
        for p in g.players.values():
            if p.role == "占い師":
                target = g.random_white(p.user_id)
                if target:
                    self.first_night_info[p.user_id] = (
                        f"🔮 初日の占い: **{target.name}** は **{RESULT_LABEL['人間']}** です。")
        await self.apply_voice("night")
        await self.set_wolf_chat(True)
        await self.channel.send(embed=discord.Embed(
            title="🌙 恐ろしい夜がやってきました(初日)",
            description="襲撃はありません。占い師には、ランダムな「人間」が1人伝えられます。",
            color=discord.Color.dark_blue()))
        await asyncio.sleep(C.FIRST_NIGHT_SECONDS)

    async def night(self) -> None:
        g = self.game
        g.phase = Phase.NIGHT
        g.reset_night()
        self.night_replies = {}
        actors = g.night_actors()
        self.pending_night = set(actors)
        self.night_event.clear()
        await self.apply_voice("night")
        await self.set_wolf_chat(True)
        msg = await self.channel.send(embed=discord.Embed(
            title=f"🌙 恐ろしい夜がやってきました({g.day}日目・{C.NIGHT_SECONDS}秒)",
            description=("行動する人は「夜の行動」を押してください(市民・霊媒師・狂人は、怪しい人アンケートです)。\n"
                         "全員が確定すると、すぐに結果が出ます。人狼は「人狼の巣」で相談できます。"),
            color=discord.Color.dark_blue()), view=NightView(self))
        if not self.pending_night:
            self.night_event.set()
        await wait_event(self.night_event, C.NIGHT_SECONDS)
        with contextlib.suppress(discord.HTTPException):
            await msg.edit(view=None)

        for uid in list(self.pending_night):           # 時間切れの行動は、ランダム
            targets = g.night_targets(uid, actors[uid])
            if targets:
                g.record_night_action(uid, actors[uid], random.choice(targets).user_id)
        self.pending_night.clear()

        res = g.resolve_night()
        g.apply_night(res)
        self.last_night = res
        await self.reveal_night_replies()
        await self.post_night_log(res)
        await asyncio.sleep(C.RESULT_DELAY)

    async def reveal_night_replies(self) -> None:
        """確定のときは何も出さず、結果は夜の終わりに一斉に表示する(確定の早さを読まれないため)。"""
        async def edit(inter: discord.Interaction, text: str) -> None:
            with contextlib.suppress(discord.HTTPException):
                await inter.edit_original_response(content=text, view=None)

        await asyncio.gather(*(edit(i, t) for i, t in self.night_replies.values()))

    async def post_night_log(self, res) -> None:
        g = self.game
        lines = [f"🌙 **{g.day}日目の夜の記録**"]
        if res.attack_target is not None:
            lines.append(f"🐺 襲撃先: {g.players[res.attack_target].name}")
        if res.guard_target is not None:
            lines.append(f"🛡️ 護衛先: {g.players[res.guard_target].name}")
        if res.divine:
            seer, target, result = res.divine
            lines.append(f"🔮 占い: {g.players[seer].name} → {g.players[target].name}({RESULT_LABEL[result]})")
        if res.attack_target is not None and res.attack_target == res.guard_target:
            lines.append("✨ 護衛成功(GJ)!")
        lines.append("結果: " + (f"{g.players[res.victim].name} が襲撃されました" if res.victim is not None
                                else "犠牲者なし"))
        if g.survey_roles_active():
            answered = g.survey_counts()
            silent = [p.name for p in g.alive_players()
                      if p.role in SURVEY_ROLES and p.user_id not in g.survey]
            lines.append("👀 怪しい人アンケート: " + (
                "、".join(f"{g.players[u].name} {c}票" for u, c in answered.most_common()) or "回答なし"))
            if silent:
                lines.append("(未回答: " + "、".join(silent) + ")")
            for uid, note in g.survey_notes.items():
                lines.append(f"🃏 {g.players[uid].name} の一言: {note}")
        await self.to_heaven("\n".join(lines))

    async def morning(self) -> None:
        g = self.game
        g.phase = Phase.DAY
        res = self.last_night
        await self.apply_voice("silent")                  # 遺言の人以外は、昼の議論が始まるまで話せない
        victim = res.victim if (res and res.victim is not None) else None
        # 次の朝までVCに戻らなかった人(襲撃された人は除く)
        gone = [uid for uid in list(self.absent) if uid != victim
                and g.players[uid].alive and not self.in_vc(self.member(uid))]
        if victim is not None:
            desc = (f"昨夜、**{g.players[victim].name}** が襲撃されました。\n"
                    f"({C.VICTIM_WILL_SECONDS}秒、進行に関係ないことだけ話せます)")
        else:
            desc = "昨夜は誰も犠牲になりませんでした。"
        if gone:
            desc += "\n\n🔌 次の朝までVCに戻らなかったため、**突然死**になりました: " + \
                    "、".join(g.players[u].name for u in gone)
        await self.channel.send(embed=discord.Embed(
            title="☀️ おはようございます", description=desc, color=discord.Color.gold()))
        if victim is not None:
            await self.speak(victim, C.VICTIM_WILL_SECONDS, after="silent")
            await self.kill(victim)
        else:
            await asyncio.sleep(C.MORNING_SECONDS)
        for uid in gone:
            nights = self.absent.pop(uid, None)
            p = g.players[uid]
            if nights is not None:
                p.nights = nights                         # 抜けた夜より後は数えない
            g.mark_quit(uid)
            await self.to_heaven(f"🔌 {p.name} が突然死しました。")
        for uid in list(self.absent):                     # 戻った・死んだ人の記録は消す
            if not g.players[uid].alive:
                self.absent.pop(uid, None)
        if gone:
            await self.check_outcome()

    # ---------- 昼 ----------
    async def day_and_vote(self) -> int:
        await self.discussion()
        return await self.vote()

    async def new_co_panel(self) -> None:
        """昼の始まりに、CO一覧とCOボタンを置き直す。"""
        if self.co_message:
            with contextlib.suppress(discord.HTTPException):
                await self.co_message.edit(view=None)
        self.co_message = await self.channel.send(embed=self.co_embed(), view=COView(self))

    def co_embed(self) -> discord.Embed:
        return discord.Embed(title="📋 CO一覧", description=self.game.co_board(), color=discord.Color.blurple())

    async def refresh_co_panel(self) -> None:
        if self.co_message:
            with contextlib.suppress(discord.HTTPException):
                await self.co_message.edit(embed=self.co_embed())

    async def discussion(self) -> None:
        g = self.game
        g.phase = Phase.DAY
        self.skip_votes, self.ext_votes = set(), set()
        self.skip_event.clear()
        self.ext_pending = False                    # 延長は1試合で2回まで(ext_used は引き継ぐ)
        await self.set_wolf_chat(False)
        await self.apply_voice("day")

        secs = ceil_to_30(len(g.alive_players()) * C.DISCUSSION_PER_PLAYER)
        alive = "、".join(p.name for p in g.alive_players())
        await self.channel.send(embed=discord.Embed(
            title=f"☀️ {g.day}日目の昼",
            description=(f"**生存者({len(g.alive_players())}人)**: {alive}\n"
                         f"議論: {secs // 60}分{secs % 60:02d}秒 / 延長は1分を{C.EXTEND_MAX}回まで(過半数で)\n"
                         f"延長はあと{C.EXTEND_MAX - self.ext_used}回\n"
                         "「投票へ進む」は、過半数でいつでも押せます。"),
            color=discord.Color.gold()), view=DayView(self))
        await self.new_co_panel()

        loop = asyncio.get_running_loop()
        deadline = loop.time() + secs
        warned = secs <= 30                          # 残り30秒の告知は、1回だけ(延長したら、また出す)
        while loop.time() < deadline and not self.skip_event.is_set():
            if self.ext_pending:
                self.ext_pending = False
                deadline += C.EXTEND_SECONDS
                warned = False
                await self.channel.send(f"⏳ 延長しました(+{C.EXTEND_SECONDS // 60}分)。")
            if not warned and deadline - loop.time() <= 30:
                warned = True
                await self.channel.send("⏰ 議論の終わりまで、あと30秒です。")
            await asyncio.sleep(1)
        await self.channel.send(embed=discord.Embed(
            title="⌛ 議論が終了しました", description="まもなく投票が始まります。", color=discord.Color.red()))

    # ---------- 投票 ----------
    async def vote(self) -> int:
        """席順で投票する。割り込み(先着)があればその人が先。同票は決選投票。戻り値は処刑される人。"""
        g = self.game
        g.phase = Phase.VOTE
        self.votes, self.jump_queue = {}, []
        await self.apply_voice("silent")                   # 投票の間は、順番の人以外は話せない
        seats = " → ".join(g.players[u].name for u in g.seats if g.players[u].alive)
        jump_msg = await self.channel.send(embed=discord.Embed(
            title="🗳️ 投票が始まります",
            description=(f"席順: {seats}\n一人ずつ、投票先を確定して理由を話します({C.VOTE_SPEECH_SECONDS}秒)。\n"
                         f"先に投票したい人は、いまのうちに「次に投票する」を押してください({C.VOTE_GRACE_SECONDS}秒)。"),
            color=discord.Color.red()), view=JumpView(self))
        await asyncio.sleep(C.VOTE_GRACE_SECONDS)

        done: set[int] = set()
        while (voter := g.vote_order(self.jump_queue, done)) is not None:
            done.add(voter)
            await self.vote_turn(voter)
        with contextlib.suppress(discord.HTTPException):
            await jump_msg.edit(view=None)

        candidates = g.alive_ids()
        counts, leaders = g.tally(self.votes, candidates)
        g.record_votes(counts)
        summary = "、".join(f"{g.players[c].name} {counts.get(c, 0)}票" for c in candidates)
        await self.channel.send(embed=discord.Embed(
            title="📊 投票結果", description=f"**{summary}**", color=discord.Color.red()))
        if len(leaders) > 1:
            leaders = await self.tie_break(leaders)
        executed = random.choice(leaders)                  # 決選でも同数ならランダム
        await self.channel.send(f"⚖️ 投票の結果、**{g.players[executed].name}** が処刑されます。")
        await self.executed_will(executed)
        return executed

    async def vote_turn(self, voter: int) -> None:
        g = self.game
        await self.channel.send(
            f"🗳️ <@{voter}> さんの番です。({C.VOTE_CONFIRM_SECONDS}秒以内)",
            view=ConfirmVoteView(self, voter, C.VOTE_CONFIRM_SECONDS + 5))
        for _ in range(C.VOTE_CONFIRM_SECONDS):
            if voter in self.votes:
                break
            await asyncio.sleep(1)
        if voter not in self.votes:
            await self.channel.send(f"⏱️ **{g.players[voter].name}** さんは時間内に確定しませんでした(棄権)。")
            return
        await self.speak(voter, C.VOTE_SPEECH_SECONDS, after=None,
                         prompt="なぜ投票したのか一言話してください")
        # ミュートに戻す処理と、結果の表示を同時にする(次の人の番を、早く出すため)
        await asyncio.gather(
            self.apply_voice("silent"),
            self.channel.send(f"📣 **{g.players[voter].name}** → **{g.players[self.votes[voter]].name}**"),
            self.to_heaven(f"🗳️ {g.players[voter].name} → {g.players[self.votes[voter]].name}"))

    async def tie_break(self, leaders: list[int]) -> list[int]:
        """決選投票。候補者は主張し、投票はできない。"""
        g = self.game
        await self.channel.send(embed=discord.Embed(
            title="🗳️ 決選投票", description="同票です: " + "、".join(g.players[c].name for c in leaders)
            + "\n候補の人は、順に短く主張してください。", color=discord.Color.red()))
        for c in leaders:
            await self.speak(c, C.TIE_SPEECH_SECONDS, after="silent", text=(
                f"<@{c}> さん、あなたは決選投票に残りました。処刑されないように弁明する時間があります。"
                f"(最大{C.TIE_SPEECH_SECONDS}秒)終わったら「意見終了」を押してください。"))
        self.votes, self.tie_candidates = {}, leaders
        await self.apply_voice("silent")
        await self.channel.send(embed=discord.Embed(
            title="🗳️ 決選の投票(候補の人は投票できません)",
            description=f"「決選に投票」を押してください({C.TIE_VOTE_SECONDS}秒)。", color=discord.Color.red()),
            view=TieVoteView(self, C.TIE_VOTE_SECONDS + 5))
        await asyncio.sleep(C.TIE_VOTE_SECONDS)
        counts, new_leaders = g.tally(self.votes, leaders)
        await self.channel.send("📊 決選の結果: " + "、".join(
            f"{g.players[c].name} {counts.get(c, 0)}票" for c in leaders))
        return new_leaders

    async def executed_will(self, uid: int) -> None:
        g = self.game
        await self.speak(uid, C.EXECUTED_WILL_SECONDS)
        g.record_execution(uid)
        await self.kill(uid)
        label = RESULT_LABEL[ROLES[g.players[uid].role].seen_as]
        self.medium_text = f"👻 処刑された **{g.players[uid].name}** は **{label}** でした。"
        await self.to_heaven(f"⚖️ 処刑: {g.players[uid].name}({g.players[uid].role})")

    # ================================================================
    # 突然死(VCから戻らない)
    # ================================================================
    async def on_voice_change(self, uid: int, joined: bool) -> None:
        if not self.started or not self.roles_ready or self.finished or not self.is_player(uid):
            return
        p = self.game.players[uid]
        if p.quit_early or p.left or not p.alive:
            return
        if joined:
            self.absent.pop(uid, None)                           # 次の朝より前に戻れば、突然死なし
            await self.set_mute(uid, self._should_mute(uid))     # 戻ったときの状態に合わせる
        elif uid not in self.absent:
            if self.game.one_night:
                await self.quit_now(uid)                         # 夜が1回だけのゲームは、その場で突然死
            else:
                self.absent[uid] = p.nights                      # 次の朝の発表で判定する

    async def sweep_absent(self) -> None:
        """役職が配られた時点で、VCにいない人を、抜けた人として扱う。"""
        for uid, p in list(self.game.players.items()):
            if p.alive and not self.in_vc(self.member(uid)):
                if self.game.one_night:
                    await self.quit_now(uid)
                else:
                    self.absent.setdefault(uid, p.nights)

    async def quit_now(self, uid: int) -> None:
        p = self.game.players[uid]
        if not p.alive or self.finished or self.in_vc(self.member(uid)):
            return
        self.game.mark_quit(uid)
        self.absent.pop(uid, None)
        await self.channel.send(f"🔌 **{p.name}** さんがVCから戻らなかったため、突然死になりました。")
        await self.to_heaven(f"🔌 {p.name} が突然死しました。")
        await self.check_outcome()

    def close_note(self) -> str:
        """結果の画面に出す、「今すぐ閉じる」の説明。"""
        if self.can_close_now(None):
            host = self.game.players.get(self.game.host_id)
            if host is not None and host.left and not host.quit_early:
                return "⚠️ 主催者がゲームから抜けたので、誰でも「今すぐ閉じる」が押せるようになりました。"
            return "⚠️ 主催者が突然死したので、誰でも「今すぐ閉じる」が押せるようになりました。"
        return "🧹 「今すぐ閉じる」は、主催者だけが押せます。"

    def can_close_now(self, uid: int | None) -> bool:
        """uid=None なら、今「誰でも」押せるかどうか。"""
        host = self.game.players.get(self.game.host_id)
        host_gone = host is not None and (host.quit_early or host.left)
        return host_gone or (uid is not None and uid == self.game.host_id)

    async def check_outcome(self) -> None:
        """抜けた・突然死したあとに呼ぶ。人狼が全員いなくなったら村人の勝ち。生きている人が3人未満なら即終了。"""
        if not self.started or self.finished:
            return
        g = self.game
        gone_wolves = [p for p in g.players.values() if p.role == "人狼" and (p.left or p.quit_early)]
        alive_wolves = [p for p in g.alive_players() if p.role == "人狼"]
        if gone_wolves and not alive_wolves:
            await self.end_early("village")
        elif len(g.alive_players()) < 3:
            await self.end_unplayable()

    async def stop_task_for_end(self) -> None:
        self.ending_early = True
        if self.task and not self.task.done() and asyncio.current_task() is not self.task:
            self.task.cancel()

    async def end_early(self, winner: str) -> None:
        """人狼が全員いなくなった。通常どおり精算して終わる。"""
        self.finished = True
        await self.stop_task_for_end()
        await self.finish(winner)

    async def end_unplayable(self) -> None:
        """生きている人が3人未満になった。最後まで残った人だけ参戦費を返す。報酬はなし。"""
        g = self.game
        self.finished = True
        g.phase = Phase.ENDED
        fee = points.fee_for(g.one_night)
        for uid in self.payers:
            p = g.players[uid]
            if not (p.left or p.quit_early):
                self.store.add(self.guild.id, uid, fee)
        await self.stop_task_for_end()
        with contextlib.suppress(discord.HTTPException):
            await self.channel.send("⚠️ 参加者が足りなくなったため、ゲームを終了します。"
                                    "最後まで残った人には参戦費を返しました(報酬はありません)。\n" + self.close_note(),
                                    view=CloseNowView(self))

    async def leave_game(self, uid: int) -> None:
        """自分から途中で抜ける(退出)。報酬なし・参戦費は返さない。"""
        p = self.game.players[uid]
        self.absent.pop(uid, None)
        self.game.mark_left(uid)
        self.pending_night.discard(uid)
        await self.set_mute(uid, True)
        await self.channel.send(f"🚪 **{p.name}** さんがゲームから抜けました。")
        await self.to_heaven(f"🚪 {p.name} が退出しました。")
        if self.game.phase == Phase.NIGHT and not self.pending_night:
            self.night_event.set()
        await self.check_outcome()

    # ================================================================
    # ワンナイト人狼(noteのルール)
    # ================================================================
    def note_seer(self, uid: int, target: int) -> str:
        """占い師の行動。target が 0 以上なら参加者1人、-1 なら墓地の2枚。結果の文を返す。"""
        g, note = self.game, self.game.note
        self.note_choices[uid] = target
        if target < 0:
            a, b = note.look_graveyard()
            text = f"🔮 墓地の2枚は **{a}** と **{b}** でした。"
        else:
            role = note.look_player(target)
            text = f"🔮 **{g.players[target].name}** は **{role}** でした。"
        return text

    def note_thief(self, uid: int, target: int | None) -> str:
        """怪盗の行動。target が None なら交換しない。夜の終わりに交換する。"""
        g, note = self.game, self.game.note
        self.note_choices[uid] = target
        if target is None:
            return "🎭 今夜は交換しません。"
        return (f"🎭 **{g.players[target].name}** と交換します。"
                f"夜の終わりに、あなたは **{note.roles[target]}** になります。")

    async def note_night(self) -> None:
        g = self.game
        g.phase = Phase.NIGHT
        g.reset_night()
        self.night_replies = {}
        self.note_choices = {}
        self.pending_night = {u for u, p in g.players.items() if p.role in ("占い師", "怪盗")}
        self.night_event.clear()
        await self.apply_voice("night")
        await self.set_wolf_chat(True)
        msg = await self.channel.send(embed=discord.Embed(
            title=f"🌙 恐ろしい夜がやってきました({C.NIGHT_SECONDS}秒)",
            description=("占い師と怪盗は「夜の行動」を押してください。"
                         "人狼は、押すと仲間が分かります。村人は何もしません。"),
            color=discord.Color.dark_blue()), view=NightView(self))
        if not self.pending_night:
            self.night_event.set()
        await wait_event(self.night_event, C.NIGHT_SECONDS)
        with contextlib.suppress(discord.HTTPException):
            await msg.edit(view=None)

        note = g.note
        for uid in list(self.pending_night):             # 時間切れは、占いはランダム・怪盗は交換しない
            if g.players[uid].role == "占い師":
                options = [t.user_id for t in g.players.values() if t.user_id != uid]
                self.note_seer(uid, random.choice(options))
            else:
                self.note_thief(uid, None)
        self.pending_night.clear()

        thief = next((u for u, p in g.players.items() if p.role == "怪盗" and u in self.note_choices), None)
        if thief is not None and self.note_choices.get(thief) is not None:
            note.swap(thief, self.note_choices[thief])
            g.sync_note_roles()
        await self.reveal_night_replies()
        await asyncio.sleep(C.RESULT_DELAY)

    async def note_vote(self) -> str:
        """投票(1人1票)。戻り値は勝った陣営("village" / "wolf" / "all")。"""
        g, note = self.game, self.game.note
        g.phase = Phase.VOTE
        self.note_votes = {}
        self.note_vote_done = asyncio.Event()
        await self.apply_voice("silent")
        msg = await self.channel.send(embed=discord.Embed(
            title=f"🗳️ 投票({C.NOTE_VOTE_SECONDS}秒)",
            description="1人1票で、追放したい人に投票してください。",
            color=discord.Color.red()), view=NoteVoteView(self))
        await wait_event(self.note_vote_done, C.NOTE_VOTE_SECONDS)
        with contextlib.suppress(discord.HTTPException):
            await msg.edit(view=None)

        winner, executed = NR.resolve_vote(note, self.note_votes)
        counts = Counter(self.note_votes.values())
        for voter, target in self.note_votes.items():
            await self.to_heaven(f"🗳️ {g.players[voter].name} → {g.players[target].name}")
        lines = [f"・{g.players[t].name}: {c}票" for t, c in counts.most_common()] or ["(投票なし)"]
        if executed:
            names_exec = "、".join(g.players[e].name for e in executed)
            roles_exec = "、".join(note.roles[e] for e in executed)
            await self.channel.send("📊 投票結果\n" + "\n".join(lines)
                                    + f"\n\n⚖️ 処刑: **{names_exec}**({roles_exec})")
            for e in executed:
                g.kill(e)
        else:
            await self.channel.send("📊 投票結果\n" + "\n".join(lines) + "\n\n票がバラバラのため、処刑はありません。")
        return winner

    # ================================================================
    # 終了・中止
    # ================================================================
    async def finish(self, winner: str) -> None:
        g = self.game
        g.phase = Phase.ENDED
        self.finished = True
        s = g.settlement(winner, self.payers)
        for uid, amount in s.final.items():
            self.store.add(self.guild.id, uid, amount)
        title = {"village": "🏘️ 村人陣営の勝利!", "wolf": "🐺 人狼陣営の勝利!",
                 "all": "🕊️ 平和村! 全員の勝利"}[winner]
        embed = discord.Embed(title=title, description="**全員の役職**\n" + g.role_summary(),
                              color=discord.Color.green())
        lines = [f"・{g.players[u].name}{'' if u in self.payers else '(免除)'}"
                 f"{'(突然死)' if g.players[u].quit_early else ''}{'(退出)' if g.players[u].left else ''}: +{a}pt" for u, a in s.final.items()]
        embed.add_field(name=f"💰 ポイント(還元率 {round(s.ratio * 100)}%)",
                        value="\n".join(lines)[:1000], inline=False)
        embed.add_field(name="📋 COの記録", value=g.history_text()[:1000], inline=False)
        embed.add_field(name="🧹 チャンネルについて", value=self.close_note(), inline=False)
        await self.channel.send(embed=embed, view=CloseNowView(self))

    def refund(self) -> None:
        if self.started and not self.finished:
            fee = points.fee_for(self.game.one_night)
            for uid in self.payers:
                self.store.add(self.guild.id, uid, fee)

    async def abort(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        else:
            await self.cleanup()

    async def cleanup(self) -> None:
        if self.cleaned:
            return
        self.cleaned = True
        await self.unlock_vc()
        await asyncio.gather(*(self.set_mute(uid, False) for uid in self.game.players))
        for m in [self.member(u) for u in self.game.players]:
            if m:
                with contextlib.suppress(discord.HTTPException):
                    await self.channel.set_permissions(m, overwrite=None)
        channels = [c for c in (self.game_channel, self.wolf_channel, self.heaven) if c]
        for c in channels:
            with contextlib.suppress(discord.HTTPException):
                await c.send(f"このチャンネルは{C.CLEANUP_SECONDS}秒後に削除されます。")
        if channels:
            with contextlib.suppress(asyncio.TimeoutError):   # この間は、生存者も天国を観戦できる
                await asyncio.wait_for(self.close_now.wait(), C.CLEANUP_SECONDS)
        if self.spectate_msg:                                  # 観戦ボタンは、天国を消すときに一緒に消す
            with contextlib.suppress(discord.HTTPException):
                await self.spectate_msg.delete()
        for c in channels:
            with contextlib.suppress(discord.HTTPException):
                await c.delete()
        self.bot.games.pop(self.guild.id, None)
