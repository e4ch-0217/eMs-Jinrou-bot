"""Discord のボタン・メニュー。処理の中身は Session のメソッドに任せ、ここは受け口だけ。"""
from __future__ import annotations

import discord

from config import HOPE_SECONDS, NIGHT_SECONDS
from roles import CO_CHOICES, ROLE_LIST


class PickView(discord.ui.View):
    """候補から1人を選ぶメニュー。選ばれたら on_pick(interaction, user_id) を呼ぶ。"""

    def __init__(self, candidates: list[tuple[int, str]], on_pick, placeholder: str, timeout: float = 180):
        super().__init__(timeout=timeout)
        options = [discord.SelectOption(label=name[:100], value=str(uid)) for uid, name in candidates[:25]]
        select = discord.ui.Select(placeholder=placeholder[:100], options=options)

        async def callback(interaction: discord.Interaction):
            await on_pick(interaction, int(select.values[0]))

        select.callback = callback
        self.add_item(select)


class ResultView(discord.ui.View):
    """占い・霊媒の結果(白/黒)を選ぶ。"""

    def __init__(self, on_choice):
        super().__init__(timeout=180)
        for label, value, style in (("人間(白)", "人間", discord.ButtonStyle.success),
                                    ("人狼(黒)", "人狼", discord.ButtonStyle.danger)):
            button = discord.ui.Button(label=label, style=style)
            button.callback = self._make(value, on_choice)
            self.add_item(button)

    @staticmethod
    def _make(value, on_choice):
        async def callback(interaction: discord.Interaction):
            await on_choice(interaction, value)
        return callback


class RoleSelectView(discord.ui.View):
    """希望役職(第1・第2)を選ぶ。決定すると希望が記録される。"""

    def __init__(self, session, uid: int):
        super().__init__(timeout=HOPE_SECONDS + 10)
        self.session, self.uid = session, uid
        self.first: str | None = None
        self.second: str | None = None
        # このゲームの役職構成にいる役職だけ出す(いない役職は選べない)
        in_game = session.game.composition()
        options = [discord.SelectOption(label=r.name, value=r.name) for r in ROLE_LIST if in_game.get(r.name)]
        options.append(discord.SelectOption(label="どれでもOK", value="any"))
        self.first_select = discord.ui.Select(placeholder="第1希望", options=options)
        self.second_select = discord.ui.Select(placeholder="第2希望", options=options)
        self.first_select.callback = self._on_first
        self.second_select.callback = self._on_second
        self.add_item(self.first_select)
        self.add_item(self.second_select)

    async def _on_first(self, interaction: discord.Interaction):
        self.first = self.first_select.values[0]
        await interaction.response.defer()

    async def _on_second(self, interaction: discord.Interaction):
        self.second = self.second_select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label="決定", style=discord.ButtonStyle.success)
    async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        pick = lambda v: None if v in (None, "any") else v  # noqa: E731
        self.session.hopes[self.uid] = (pick(self.first), pick(self.second))
        await interaction.response.send_message("希望を受け付けました(公開されません)。", ephemeral=True)


class HopeView(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=HOPE_SECONDS + 5)
        self.session = session

    @discord.ui.button(label="希望を出す", style=discord.ButtonStyle.primary, emoji="🎲")
    async def hope(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.session.is_player(interaction.user.id):
            await interaction.response.send_message("参加者だけが選べます。", ephemeral=True)
            return
        await interaction.response.send_message(
            "第1・第2希望を選んで「決定」を押してください。",
            view=RoleSelectView(self.session, interaction.user.id), ephemeral=True)


class GameView(discord.ui.View):
    """ゲーム中の常設ボタン: 役職の確認と観戦。"""

    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="役職を確認", style=discord.ButtonStyle.secondary, emoji="🪪")
    async def role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.show_role(interaction)

    @discord.ui.button(label="観戦する", style=discord.ButtonStyle.secondary, emoji="👀")
    async def spectate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.join_spectators(interaction)

    @discord.ui.button(label="ゲームから抜ける", style=discord.ButtonStyle.danger, emoji="🚪")
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.request_leave(interaction)


class NoteModal(discord.ui.Modal, title="狂人の一言(任意)"):
    """狂人が、アンケートと一緒に天国へ送る一言。空欄でもOK。"""

    note = discord.ui.TextInput(label="一言(空欄でもOK)", required=False, max_length=100,
                                style=discord.TextStyle.short)

    def __init__(self, on_submit):
        super().__init__()
        self._on_submit = on_submit

    async def on_submit(self, interaction: discord.Interaction):
        await self._on_submit(interaction, str(self.note.value or ""))


class ConfirmLeaveView(discord.ui.View):
    """ゲームから抜ける前の確認。"""

    def __init__(self, session, uid: int):
        super().__init__(timeout=60)
        self.session, self.uid = session, uid

    @discord.ui.button(label="抜ける", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="ゲームから抜けました。", view=None)
        await self.session.leave_game(self.uid)

    @discord.ui.button(label="やめる", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="続けます。", view=None)


class CloseNowView(discord.ui.View):
    """終了後、主催者が天国・進行・人狼の巣をすぐ閉じるボタン。"""

    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="今すぐ閉じる", style=discord.ButtonStyle.secondary, emoji="🧹")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.session.game.host_id:
            await interaction.response.send_message("主催者だけが押せます。", ephemeral=True)
            return
        self.session.close_now.set()
        await interaction.response.send_message("チャンネルを閉じます。", ephemeral=True)


class ConfirmCOView(discord.ui.View):
    """人狼陣営の役職にCOするときの、最後の確認。"""

    def __init__(self, handlers, uid: int, role: str):
        super().__init__(timeout=60)
        self.handlers, self.uid, self.role = handlers, uid, role

    @discord.ui.button(label="はい、COする", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("この確認は、COした本人だけが押せます。", ephemeral=True)
            return
        if await self.handlers.commit_co(interaction, self.role):
            await interaction.response.edit_message(content="記録しました。", view=None)

    @discord.ui.button(label="やめる", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="COをやめました。", view=None)


class NoteVoteView(discord.ui.View):
    """ワンナイト人狼の投票ボタン。"""

    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="投票する", style=discord.ButtonStyle.danger, emoji="🗳️")
    async def vote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.note_open_vote(interaction)


class NightView(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=NIGHT_SECONDS + 10)
        self.session = session

    @discord.ui.button(label="夜の行動", style=discord.ButtonStyle.primary, emoji="🌙")
    async def act(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.night_menu(interaction)


class DayView(discord.ui.View):
    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="投票へ進む(過半数)", style=discord.ButtonStyle.secondary, emoji="⏩")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.request_skip(interaction)

    @discord.ui.button(label="延長(1分・過半数)", style=discord.ButtonStyle.secondary, emoji="➕")
    async def extend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.request_extend(interaction)


class COView(discord.ui.View):
    """CO一覧の下に出る、ワンタップのCOボタン。"""

    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session
        in_game = {p.role for p in session.game.players.values()}
        # ゲームにいる役職だけ、COの候補に出す(「撤回」は常に出す)
        for role in [r for r in CO_CHOICES if r in in_game] + ["撤回"]:
            label = "撤回" if role == "撤回" else f"{role}CO"
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
            button.callback = self._make(role)
            self.add_item(button)
        report = discord.ui.Button(label="結果を報告", style=discord.ButtonStyle.primary, emoji="📣")
        report.callback = self._report
        self.add_item(report)

    def _make(self, role: str):
        async def callback(interaction: discord.Interaction):
            await self.session.handle_co(interaction, role)
        return callback

    async def _report(self, interaction: discord.Interaction):
        await self.session.open_report_menu(interaction)


class JumpView(discord.ui.View):
    """投票中の「次に投票する」ボタン。押した順に、席順より先に回ってくる。"""

    def __init__(self, session):
        super().__init__(timeout=None)
        self.session = session

    @discord.ui.button(label="次に投票する(割り込み)", style=discord.ButtonStyle.danger, emoji="🙋")
    async def jump(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.request_jump(interaction)


class ConfirmVoteView(discord.ui.View):
    def __init__(self, session, voter: int, timeout: float):
        super().__init__(timeout=timeout)
        self.session, self.voter = session, voter

    @discord.ui.button(label="投票先を確定", style=discord.ButtonStyle.danger, emoji="🗳️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.open_vote_menu(interaction, self.voter)


class SpeakEndView(discord.ui.View):
    """話している人だけが押せる「意見終了」。"""

    def __init__(self, session, speaker: int):
        super().__init__(timeout=None)
        self.session, self.speaker = session, speaker

    @discord.ui.button(label="意見終了", style=discord.ButtonStyle.primary, emoji="🎤")
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.speaker:
            await interaction.response.send_message("いまは話している人だけが押せます。", ephemeral=True)
            return
        self.session.speak_end.set()
        await interaction.response.send_message("意見終了しました。", ephemeral=True)


class TieVoteView(discord.ui.View):
    def __init__(self, session, timeout: float):
        super().__init__(timeout=timeout)
        self.session = session

    @discord.ui.button(label="決選に投票", style=discord.ButtonStyle.danger, emoji="🗳️")
    async def vote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.session.open_tie_menu(interaction)
