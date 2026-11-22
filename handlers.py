"""ボタンを押されたときの処理(Session に混ぜて使う)。"""
from __future__ import annotations

import contextlib

import discord

import config as C
import note_rules as NR
from game import NO_ATTACK, PEACE_VOTE, RESULT_LABEL, Mode, Phase
from roles import ATTACKERS, NOTE_ONLY_ROLES, ROLES, SURVEY_ROLES, WOLF_CHAT_ROLES, WOLF_SIDE_CO
from views import ConfirmCOView, ConfirmGreedyView, ConfirmLeaveView, NoteModal, PickView, ResultView


async def deny(interaction: discord.Interaction, text: str) -> None:
    await interaction.response.send_message(text, ephemeral=True)


class Handlers:
    # ---------- 役職の確認・観戦 ----------
    def role_text(self, uid: int) -> str:
        g = self.game
        p = g.players[uid]
        role = ROLES[p.role]
        text = f"{role.emoji} あなたの役職は **{role.name}** です({g.mode.value})。\n\n{role.description}"
        if p.role in ATTACKERS:
            if self.wolf_channel:
                text += "\n夜の間だけ人狼の巣で作戦会議ができます。"
            mates = [w.name for w in g.players.values() if w.role in ATTACKERS and w.user_id != uid]
            text += "\n\n仲間の人狼: " + ("、".join(mates) if mates else "いません(あなた1人です)")
            whisperers = [w.name for w in g.players.values() if w.role == "囁き狂人"]
            if whisperers:
                text += "\n囁き狂人(あなたたちの味方です): " + "、".join(whisperers)
        elif p.role in ("狂信者", "囁き狂人"):
            wolves = [w.name for w in g.players.values() if w.role in ATTACKERS]
            text += "\n\n人狼: " + "、".join(wolves)
            if p.role == "囁き狂人" and self.wolf_channel:
                text += "\n夜の間だけ人狼の巣で、人狼と相談できます。"
            else:
                text += "\n(人狼とは会話できません。人狼からは、あなたが誰か分かりません)"
        if p.role in ("共有者", "囁き共有者"):                 # 2人1組。組の相手だけ分かる
            mate = g.partner.get(uid)
            text += "\n\n組の相手: " + (g.players[mate].name if mate in g.players else "いません")
            if p.role == "囁き共有者" and self.share_channels:
                text += "\n夜の間だけ、組の2人だけの「共有者の巣」で相談できます。"
        if p.role == "強欲な人狼":
            text += "\n\n" + ("能力は使用済みです。" if p.greedy_used
                                else "能力(1ゲーム1回): 夜の「夜の行動」から「能力を使う」を選ぶと、もう1人を襲撃できます。")
        if p.role == "盟約者":
            team_name = {"village": "村人陣営", "wolf": "人狼陣営"}
            if p.pact_partner is None:
                text += "\n\n盟約先: まだ選んでいません(初日の夜に、「夜の行動」から選べます)。"
            else:
                partner = g.players[p.pact_partner]
                state = "" if partner.alive else "(死亡・次の夜に選び直します)"
                text += (f"\n\n盟約先: **{partner.name}**{state}"
                         f"\n盟約先の役職: **{ROLES[partner.role].name}**"
                         f"\n今の陣営: **{team_name[g.team_of(p)]}**(盟約先の陣営で動きます)")
        for line in self.seer_results.get(uid, []):
            text += "\n\n" + line
        if uid in self.first_night_info:
            text += "\n\n" + self.first_night_info[uid]
        if p.role == "霊媒師" and self.medium_text:
            text += "\n\n" + self.medium_text
        return text

    async def show_role(self, interaction: discord.Interaction) -> None:
        if not self.started or not self.is_player(self.actor(interaction.user.id)):
            await deny(interaction, "参加者だけが見られます。")
            return
        await deny(interaction, self.role_text(self.actor(interaction.user.id)))

    async def request_leave(self, interaction: discord.Interaction) -> None:
        p = self.game.players.get(self.actor(interaction.user.id))
        if not self.started or self.finished or p is None or p.left or p.quit_early:
            await deny(interaction, "ゲームに参加していません。")
            return
        await interaction.response.send_message(
            "ゲームから抜けると、**報酬はなし・参戦費も返しません**。本当に抜けますか?",
            view=ConfirmLeaveView(self, self.actor(interaction.user.id)), ephemeral=True)

    async def join_spectators(self, interaction: discord.Interaction) -> None:
        p = self.game.players.get(self.actor(interaction.user.id))
        if p and p.alive and not self.finished:        # 終わったあとは、生存者も観戦できる
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
        uid = self.actor(interaction.user.id)
        p = g.players.get(uid)
        if p is None or not p.alive:
            await deny(interaction, "生存している参加者だけが選べます。")
            return
        if g.phase != Phase.NIGHT:
            await deny(interaction, "いまは夜ではありません。")
            return
        if p.role == "強欲な人狼":
            await self.greedy_menu(interaction)
            return
        if g.mode == Mode.NOTE:
            await self.note_night_menu(interaction)
            return
        actors = g.night_actors()

        if uid in actors:
            if uid not in self.pending_night:
                await deny(interaction, "すでに確定しています。")
                return
            act = actors[uid]
            title = {"attack": "襲撃する相手", "divine": "占う相手", "guard": "護衛する相手",
                     "pact": "盟約先"}[act]
            targets = [(t.user_id, t.name) for t in g.night_targets(uid, act)]
            if act == "attack" and g.skip_available():
                targets.append((NO_ATTACK, "襲撃しない"))
            if not targets:
                await deny(interaction, "選べる相手がいません。")
                return

            async def on_pick(i: discord.Interaction, target: int) -> None:
                result = g.record_night_action(uid, act, target)
                name = g.target_name(target)
                if act == "divine":
                    # 結果は、夜が終わるときに一斉に表示する
                    self.night_replies[uid] = (
                        i, f"🔮 **{name}** を占いました。結果: **{RESULT_LABEL[result]}**")
                    text = f"🔮 **{name}** を占います。結果は夜の終わりに表示されます。"
                elif act == "guard":
                    text = f"🛡️ **{name}** を護衛します。"
                elif act == "pact":
                    role = ROLES[g.players[target].role].name
                    text = (f"🤝 **{name}** を盟約先に選びました。\n"
                            f"盟約先の役職は **{role}** です。今から、盟約先の陣営({'村人陣営' if g.team_of(g.players[uid]) == 'village' else '人狼陣営'})で動きます。")
                elif target == NO_ATTACK:
                    text = "🌙 **襲撃しない** を選びました。"
                else:
                    text = f"🐺 **{name}** を襲撃先に選びました。"
                await i.response.edit_message(content=text + "\n(確定しました)", view=None)
                self.pending_night.discard(uid)
                if not self.pending_night:
                    self.night_event.set()

            await interaction.response.send_message(
                f"{title}を選んでください。", view=PickView(targets, on_pick, title), ephemeral=True)
            return

        if self.in_first_night:
            await deny(interaction, "初日の夜は、盟約者だけが行動できます。")
            return

        if p.role in SURVEY_ROLES and g.survey_roles_active():
            question = ("誰が一番人狼に見えますか?" if p.role == "狂人" else "誰が怪しく見えますか?")

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

        if p.role in NOTE_ONLY_ROLES and g.survey_roles_active():
            async def on_only_note(mi: discord.Interaction, note: str) -> None:
                if note.strip():
                    g.survey_notes[uid] = note.strip()[:100]
                await mi.response.send_message("一言を受け付けました(天国に送られます)。", ephemeral=True)

            await interaction.response.send_modal(NoteModal(on_only_note))
            return

        await deny(interaction, "今夜は、あなたにできる行動はありません。")

    async def note_night_menu(self, interaction: discord.Interaction) -> None:
        g, note = self.game, self.game.note
        uid = self.actor(interaction.user.id)
        p = g.players[uid]
        if p.role == "人狼":
            mates = [g.players[u].name for u in note.wolf_partners(uid)]
            await deny(interaction, "仲間の人狼: " + ("、".join(mates) if mates
                                                     else "いません(墓地に、もう1人の人狼がいるかもしれません)"))
            return
        if uid not in self.pending_night:
            await deny(interaction, "今夜は、あなたにできる行動はないか、すでに確定しています。")
            return
        if p.role == "占い師":
            options = [(t.user_id, t.name) for t in g.players.values() if t.user_id != uid]
            options += [(-1, "墓地の2枚(両方)")]

            async def on_pick(i: discord.Interaction, target: int) -> None:
                text = self.note_seer(uid, target)
                self.night_replies[uid] = (i, text)
                await i.response.edit_message(content="🔮 占いました。結果は夜の終わりに表示されます。", view=None)
                self.pending_night.discard(uid)
                if not self.pending_night:
                    self.night_event.set()

            await interaction.response.send_message(
                "占う相手を選んでください。", view=PickView(options, on_pick, "占う相手"), ephemeral=True)
            return
        # 怪盗
        options = [(t.user_id, t.name) for t in g.players.values() if t.user_id != uid]
        options.append((-3, "交換しない"))

        async def on_thief(i: discord.Interaction, target: int) -> None:
            text = self.note_thief(uid, None if target == -3 else target)
            self.night_replies[uid] = (i, text)
            await i.response.edit_message(content="🎭 決めました。結果は夜の終わりに表示されます。", view=None)
            self.pending_night.discard(uid)
            if not self.pending_night:
                self.night_event.set()

        await interaction.response.send_message(
            "交換する相手を選んでください。", view=PickView(options, on_thief, "交換する相手"), ephemeral=True)

    async def greedy_menu(self, interaction: discord.Interaction) -> None:
        """強欲な人狼の夜の行動: 通常の襲撃先(多数決に入る)と、能力(もう1人を襲撃・1ゲーム1回)。"""
        g = self.game
        uid = self.actor(interaction.user.id)
        p = g.players[uid]
        options: list[tuple[int, str]] = []
        if uid in self.pending_night:
            options += [(t.user_id, t.name) for t in g.night_targets(uid, "attack")]
            if g.skip_available():
                options.append((NO_ATTACK, "襲撃しない"))
        if not p.greedy_used:
            options.append((-2, "能力を使う(もう1人を襲撃)"))
        if not options:
            await deny(interaction, "今夜は、もう行動を決めています。")
            return

        async def on_pick(i: discord.Interaction, target: int) -> None:
            if target == -2:
                async def on_greedy(j: discord.Interaction, t: int) -> None:
                    await j.response.edit_message(
                        content=f"**{g.players[t].name}** を、能力を使って襲撃します。**取り消せません**。よろしいですか?",
                        view=ConfirmGreedyView(self, uid, t))

                await i.response.edit_message(
                    content="能力で、もう1人の襲撃先を選んでください。",
                    view=PickView([(t.user_id, t.name) for t in g.night_targets(uid, "attack")],
                                  on_greedy, "もう1人の襲撃先"))
                return
            g.record_night_action(uid, "attack", target)
            text = ("🌙 **襲撃しない** を選びました。" if target == NO_ATTACK
                    else f"🐺 **{g.target_name(target)}** を襲撃先に選びました。")
            await i.response.edit_message(content=text + "\n(確定しました)", view=None)
            self.pending_night.discard(uid)
            if not self.pending_night:
                self.night_event.set()

        await interaction.response.send_message("行動を選んでください。", view=PickView(options, on_pick, "行動"),
                                                ephemeral=True)

    async def commit_greedy(self, interaction: discord.Interaction, uid: int, target: int) -> None:
        """能力を確定する。人狼の巣に知らせる。取り消しはできない。"""
        g = self.game
        p = g.players.get(uid)
        if p is None or not p.alive or p.greedy_used or g.phase != Phase.NIGHT:
            await interaction.response.edit_message(content="今は能力を使えません。", view=None)
            return
        g.record_greedy(uid, target)
        tname = g.players[target].name
        if self.wolf_channel:
            with contextlib.suppress(discord.HTTPException):
                await self.wolf_channel.send(f"⚔️ **{p.name}** が強欲な人狼の能力を使って、**{tname}** を襲撃します。")
        await interaction.response.edit_message(
            content=f"⚔️ **{tname}** を、能力で襲撃することにしました。(取り消せません)", view=None)

    async def note_open_vote(self, interaction: discord.Interaction) -> None:
        g, note = self.game, self.game.note
        uid = self.actor(interaction.user.id)
        p = g.players.get(uid)
        if p is None or not p.alive or g.phase != Phase.VOTE:
            await deny(interaction, "いまは投票できません。")
            return
        if uid in self.note_votes:
            await deny(interaction, "すでに投票しています。")
            return
        options = [(t.user_id, t.name) for t in g.players.values() if t.alive and t.user_id != uid]
        if not note.wolves():
            options.append((NR.PEACE, "平和村を願う"))

        async def on_pick(i: discord.Interaction, target: int) -> None:
            self.note_votes[uid] = target
            await i.response.edit_message(content="投票しました。", view=None)
            tname = "平和村を願う" if target == NR.PEACE else g.players[target].name
            await self.to_heaven(f"🗳️ {g.players[uid].name} → {tname}")
            if len(self.note_votes) >= len(g.alive_ids()) and not self.note_vote_done.is_set():
                self.note_vote_done.set()

        await interaction.response.send_message(
            "投票先を選んでください。", view=PickView(options, on_pick, "投票先"), ephemeral=True)

    def survey_targets(self, uid: int) -> list[tuple[int, str]]:
        return [(p.user_id, p.name) for p in self.game.alive_players() if p.user_id != uid]

    # ---------- 昼 ----------
    def _alive_in_phase(self, interaction: discord.Interaction, phase: Phase) -> str | None:
        """使えない理由があれば返す。"""
        p = self.game.players.get(self.actor(interaction.user.id))
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
        uid = self.actor(interaction.user.id)
        if uid in self.skip_votes:
            await deny(interaction, "すでに希望しています。")
            return
        self.skip_votes.add(uid)
        need = self.majority()
        await self.channel.send(f"⏩ 投票へ進む希望: {len(self.skip_votes)}/{need}(<@{uid}> さん)")
        await interaction.response.send_message("希望を受け付けました。", ephemeral=True)
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
        uid = self.actor(interaction.user.id)
        if uid in self.ext_votes:
            await deny(interaction, "すでに希望しています。")
            return
        self.ext_votes.add(uid)
        need = self.majority()
        await self.channel.send(f"⏳ 延長の希望: {len(self.ext_votes)}/{need}(<@{uid}> さん)")
        await interaction.response.send_message("希望を受け付けました。", ephemeral=True)
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
        uid = self.actor(interaction.user.id)
        if uid in self.votes or uid in self.jump_queue:
            await deny(interaction, "すでに投票したか、予約しています。")
            return
        self.jump_queue.append(uid)
        await deny(interaction, f"予約しました(割り込み {len(self.jump_queue)}番目)。")

    async def open_vote_menu(self, interaction: discord.Interaction, voter: int) -> None:
        if self.actor(interaction.user.id) != voter:
            await deny(interaction, "あなたの番ではありません。")
            return
        if voter in self.votes:
            await deny(interaction, "すでに確定しています。")
            return
        g = self.game
        candidates = [(u, g.players[u].name) for u in g.alive_ids() if u != voter]
        if g.peace_available():
            candidates.append((PEACE_VOTE, "誰も追放しない"))

        async def on_pick(i: discord.Interaction, target: int) -> None:
            self.votes[voter] = target
            await i.response.edit_message(
                content=f"🗳️ **{g.target_name(target)}** に確定しました。理由を話してください。", view=None)
            await self.to_heaven(f"🗳️ {g.players[voter].name} → {g.target_name(target)}")

        await interaction.response.send_message(
            f"⏳ {C.VOTE_CONFIRM_SECONDS}秒以内に投票先を確定してください。\n"
            "投票先を選んでください(確定後は変えられません)。",
            view=PickView(candidates, on_pick, "処刑する人を選ぶ"), ephemeral=True)

    async def open_tie_menu(self, interaction: discord.Interaction) -> None:
        g = self.game
        uid = self.actor(interaction.user.id)
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
        p = g.players.get(self.actor(interaction.user.id))
        if p is None or not p.alive:
            await deny(interaction, "生存している参加者だけがCOできます。")
            return
        if g.phase not in (Phase.DAY, Phase.VOTE):
            await deny(interaction, "COは昼の間だけできます。")
            return
        if role != "撤回" and role not in {q.role for q in g.players.values()}:
            await deny(interaction, "その役職は、このゲームにいません。")
            return
        if p.role == "市民" and role in ("占い師", "霊媒師", "騎士"):
            await interaction.response.send_message(
                f"⚠️ あなたは市民です。{role}をCOすると、**役職騙り**になります。役職騙りしますか?",
                view=ConfirmCOView(self, p.user_id, role), ephemeral=True)
            return
        if role in WOLF_SIDE_CO:
            text = "⚠️ あなたは人狼陣営として、カミングアウトしようとしています。本当によろしいですか?"
            if g.team_of(p) == "village":
                text += "\n🚨 あなたは村人陣営です。人狼陣営の役職をCOすると、**偽CO**になります。"
            await interaction.response.send_message(
                text, view=ConfirmCOView(self, p.user_id, role), ephemeral=True)
            return
        if await self.commit_co(interaction, role):
            await deny(interaction, "記録しました。")

    async def commit_co(self, interaction: discord.Interaction, role: str) -> bool:
        """COを記録して、全員に知らせる。失敗したときは、ここで返事をして False を返す。"""
        g = self.game
        p = g.players.get(self.actor(interaction.user.id))
        if p is None or not p.alive or g.phase not in (Phase.DAY, Phase.VOTE):
            await deny(interaction, "今はCOできません。")
            return False
        text = g.set_co(p.user_id, role)
        # COは全員に見せる(通知は通常のメッセージで、CO一覧も更新する)
        await self.channel.send(text)
        await self.refresh_co_panel()
        return True

    async def open_report_menu(self, interaction: discord.Interaction) -> None:
        g = self.game
        uid = self.actor(interaction.user.id)
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
