"""ボタンを押されたときの処理(Session に混ぜて使う)。"""
from __future__ import annotations

import contextlib

import discord

from game import RESULT_LABEL, Phase
from roles import ROLES, SURVEY_ROLES
from views import ConfirmLeaveView, NoteModal, PickView, ResultView


async def deny(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message(text, ephemeral=True)


class Handlers:
    # ---------- 役職の確認・観戦 ----------
    def role_text(self, uid: int) -> str:
        g = self.game
        p = g.players[uid]
        role = ROLES[p.role]
        text = f"{role.emoji} あなたの役職は **{role.name}** です({g.mode.value})。\n\n{role.description}"
        if p.role == "人狼":
            mates = [w.name for w in g.players.values() if w.role == "人狼" and w.user_id != uid]
            text += "\n\n仲間の人狼: " + ("、".join(mates) if mates else "いません(あなた1人です)")
        if uid in self.first_night_info:
            text += "\n\n" + self.first_night_info[uid]
        if p.role == "霊媒師" and self.medium_text:
            text += "\n\n" + self.medium_text
        return text

    async def show_role(self, interaction: discord.Interaction) -> None:
        if not self.started or not self.is_player(interaction.user.id):
            await deny(interaction, "参加者だけが見られます。")
            return
        await deny(interaction, self.role_text(interaction.user.id))

    async def request_leave(self, interaction: discord.Interaction) -> None:
        p = self.game.players.get(interaction.user.id)
        if not self.started or self.finished or p is None or p.left or p.quit_early:
            await deny(interaction, "ゲームに参加していません。")
            return
        await interaction.response.send_message(
            "ゲームから抜けると、**報酬はなし・参戦費も返しません**。本当に抜けますか?",
            view=ConfirmLeaveView(self, interaction.user.id), ephemeral=True)

    async def join_spectators(self, interaction: discord.Interaction) -> None:
        p = self.game.players.get(interaction.user.id)
        if p and p.alive:
            await deny(interaction, "生存中の参加者は観戦できません。")
            return
        if self.heaven is None:
            await deny(interaction, "天国チャンネルがありません。")
            return
        with contextlib.suppress(discord.HTTPException):
            await self.heaven.set_permissions(interaction.user, view_channel=True, send_messages=True)
        await deny(interaction, f"{self.heaven.mention} から観戦できます。VCでは話せません。")

    # ---------- 夜 ----------
    async def night_menu(self, interaction: discord.Interaction) -> None:
        g = self.game
        uid = interaction.user.id
        p = g.players.get(uid)
        if p is None or not p.alive:
            await deny(interaction, "生存している参加者だけが選べます。")
            return
        if g.phase != Phase.NIGHT:
            await deny(interaction, "いまは夜ではありません。")
            return
        actors = g.night_actors()

        if uid in actors:
            if uid not in self.pending_night:
                await deny(interaction, "すでに確定しています。")
                return
            act = actors[uid]
            title = {"attack": "襲撃する相手", "divine": "占う相手", "guard": "護衛する相手"}[act]
            targets = [(t.user_id, t.name) for t in g.night_targets(uid, act)]
            if not targets:
                await deny(interaction, "選べる相手がいません。")
                return

            async def on_pick(i: discord.Interaction, target: int) -> None:
                result = g.record_night_action(uid, act, target)
                name = g.players[target].name
                if act == "divine":
                    # 結果は、夜が終わるときに一斉に表示する
                    self.night_replies[uid] = (
                        i, f"🔮 **{name}** を占いました。結果: **{RESULT_LABEL[result]}**")
                    text = f"🔮 **{name}** を占います。結果は夜の終わりに表示されます。"
                elif act == "guard":
                    text = f"🛡️ **{name}** を護衛します。"
                else:
                    text = f"🐺 **{name}** を襲撃先に選びました。"
                await i.response.edit_message(content=text + "\n(確定しました)", view=None)
                self.pending_night.discard(uid)
                if not self.pending_night:
                    self.night_event.set()

            await interaction.response.send_message(
                f"{title}を選んでください。", view=PickView(targets, on_pick, title), ephemeral=True)
            return

        if p.role in SURVEY_ROLES and g.survey_roles_active():
            question = ("誰が一番人狼だと思いますか?" if p.role == "狂人" else "誰が怪しく見えますか?")

            async def on_survey(i: discord.Interaction, target: int) -> None:
                name = g.players[target].name
                if p.role == "狂人":
                    g.record_survey(uid, target)

                    async def on_note(mi: discord.Interaction, note: str) -> None:
                        g.record_survey(uid, target, note)
                        await mi.response.edit_message(
                            content=f"🃏 **{name}** を選びました(回答と一言は天国に送られます)。", view=None)

                    await i.response.send_modal(NoteModal(on_note))
                    return
                g.record_survey(uid, target)
                await i.response.edit_message(
                    content=f"👀 **{name}** を選びました(回答は天国に送られます)。", view=None)

            await interaction.response.send_message(
                question, view=PickView(self.survey_targets(uid), on_survey, "選んでください"), ephemeral=True)
            return

        await deny(interaction, "今夜は、あなたにできる行動はありません。")

    def survey_targets(self, uid: int) -> list[tuple[int, str]]:
        return [(p.user_id, p.name) for p in self.game.alive_players() if p.user_id != uid]

    # ---------- 昼 ----------
    def _alive_in_phase(self, interaction: discord.Interaction, phase: Phase) -> str | None:
        """使えない理由があれば返す。"""
        p = self.game.players.get(interaction.user.id)
        if p is None or not p.alive:
            return "生存している参加者だけが使えます。"
        if self.game.phase != phase:
            return "いまは使えません。"
        return None

    async def request_skip(self, interaction: discord.Interaction) -> None:
        reason = self._alive_in_phase(interaction, Phase.DAY)
        if reason:
            await deny(interaction, reason)
            return
        self.skip_votes.add(interaction.user.id)
        need = self.majority()
        await deny(interaction, f"投票へ進む希望: {len(self.skip_votes)}/{need}")
        if len(self.skip_votes) >= need:
            self.skip_event.set()

    async def request_extend(self, interaction: discord.Interaction) -> None:
        reason = self._alive_in_phase(interaction, Phase.DAY)
        if reason:
            await deny(interaction, reason)
            return
        from config import EXTEND_MAX
        if self.ext_used >= EXTEND_MAX:
            await deny(interaction, f"延長は1試合{EXTEND_MAX}回までです。")
            return
        self.ext_votes.add(interaction.user.id)
        need = self.majority()
        await deny(interaction, f"延長の希望: {len(self.ext_votes)}/{need}")
        if len(self.ext_votes) >= need:
            self.ext_used += 1
            self.ext_votes = set()
            self.ext_pending = True

    # ---------- 投票 ----------
    async def request_jump(self, interaction: discord.Interaction) -> None:
        reason = self._alive_in_phase(interaction, Phase.VOTE)
        if reason:
            await deny(interaction, reason)
            return
        uid = interaction.user.id
        if uid in self.votes or uid in self.jump_queue:
            await deny(interaction, "すでに投票したか、予約しています。")
            return
        self.jump_queue.append(uid)
        await deny(interaction, f"予約しました(割り込み {len(self.jump_queue)}番目)。")

    async def open_vote_menu(self, interaction: discord.Interaction, voter: int) -> None:
        if interaction.user.id != voter:
            await deny(interaction, "あなたの番ではありません。")
            return
        if voter in self.votes:
            await deny(interaction, "すでに確定しています。")
            return
        g = self.game
        candidates = [(u, g.players[u].name) for u in g.alive_ids() if u != voter]

        async def on_pick(i: discord.Interaction, target: int) -> None:
            self.votes[voter] = target
            await i.response.edit_message(
                content=f"🗳️ **{g.players[target].name}** に確定しました。理由を話してください。", view=None)

        await interaction.response.send_message(
            "投票先を選んでください(確定後は変えられません)。",
            view=PickView(candidates, on_pick, "処刑する人を選ぶ"), ephemeral=True)

    async def open_tie_menu(self, interaction: discord.Interaction) -> None:
        g = self.game
        uid = interaction.user.id
        p = g.players.get(uid)
        if p is None or not p.alive:
            await deny(interaction, "生存している参加者だけが投票できます。")
            return
        if uid in self.tie_candidates:
            await deny(interaction, "決選の候補者は投票できません。")
            return
        candidates = [(c, g.players[c].name) for c in self.tie_candidates]

        async def on_pick(i: discord.Interaction, target: int) -> None:
            self.votes[uid] = target
            await i.response.edit_message(content="🗳️ 決選の投票を受け付けました。", view=None)

        await interaction.response.send_message(
            "候補から選んでください。", view=PickView(candidates, on_pick, "決選で投票"), ephemeral=True)

    # ---------- CO と報告 ----------
    async def handle_co(self, interaction: discord.Interaction, role: str) -> None:
        g = self.game
        p = g.players.get(interaction.user.id)
        if p is None or not p.alive:
            await deny(interaction, "生存している参加者だけがCOできます。")
            return
        if g.phase not in (Phase.DAY, Phase.VOTE):
            await deny(interaction, "COは昼の間だけできます。")
            return
        text = g.set_co(p.user_id, role)
        await deny(interaction, text.replace("📣 ", ""))
        await self.refresh_co_panel()

    async def open_report_menu(self, interaction: discord.Interaction) -> None:
        g = self.game
        uid = interaction.user.id
        p = g.players.get(uid)
        if p is None or not p.alive:
            await deny(interaction, "生存している参加者だけが報告できます。")
            return
        if g.co.get(uid) not in ("占い師", "霊媒師"):
            await deny(interaction, "先に占い師か霊媒師としてCOしてください。")
            return
        targets = [(t.user_id, t.name) for t in g.players.values() if t.user_id != uid]

        async def on_target(i: discord.Interaction, target: int) -> None:
            async def on_result(j: discord.Interaction, result: str) -> None:
                ok, text = g.add_report(uid, target, result)
                await j.response.edit_message(content=("記録しました。" if ok else text), view=None)
                if ok:
                    await self.refresh_co_panel()

            await i.response.edit_message(
                content=f"**{g.players[target].name}** の結果は?", view=ResultView(on_result))

        await interaction.response.send_message(
            "誰の結果を報告しますか?", view=PickView(targets, on_target, "対象を選ぶ"), ephemeral=True)
