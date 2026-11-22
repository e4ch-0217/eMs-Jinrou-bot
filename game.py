"""人狼のルール本体。Discord には依存しないので、単体でテストできます。"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from enum import Enum

import note_rules as NR
import points
from roles import (
    ATTACKERS, CO_CHOICES, ROLES, SURVEY_ROLES, composition_text, default_composition, validate_composition,
)

MIN_PLAYERS = 3
PEACE_VOTE = -1      # 投票の「誰も追放しない」(平和主義者が生きている間だけ出る)
FULL_MIN_PLAYERS = 6   # この人数以上なら人狼ゲーム、未満ならワンナイト
MAX_PLAYERS = 20
RESULT_LABEL = {"人間": "人間(白)", "人狼": "人狼(黒)"}


class Phase(Enum):
    LOBBY = "lobby"
    NIGHT = "night"
    DAY = "day"
    VOTE = "vote"
    ENDED = "ended"


class Mode(Enum):
    FULL = "人狼ゲーム"
    ONE_NIGHT = "簡略化人狼"
    NOTE = "ワンナイト人狼"


@dataclass
class Player:
    user_id: int
    name: str
    role: str = ""
    alive: bool = True
    nights: int = 0           # 夜を越えた回数
    kills: int = 0            # 人狼: 襲撃に成功した回数
    gj: int = 0               # 騎士: 襲撃を防いだ回数
    votes_received: int = 0   # 処刑投票で入った票の合計
    quit_early: bool = False  # 突然死したか(VCに戻らなかった)
    left: bool = False        # 自分から途中で抜けたか(報酬なし・参戦費は返さない)
    greedy_used: bool = False # 強欲な人狼: 能力を使ったか(1ゲーム1回)


@dataclass
class NightResult:
    attack_target: int | None
    guard_target: int | None
    victim: int | None
    divine: tuple[int, int, str] | None   # (占い師, 対象, 結果)
    greedy_target: int | None = None      # 強欲な人狼の能力の対象(使ったときだけ)
    greedy_victim: int | None = None      # 強欲の対象で、護衛されずに死ぬ人
    gj: bool = False                      # 護衛が、どちらかの襲撃を防いだ

    @property
    def victims(self) -> list[int]:
        return [v for v in (self.victim, self.greedy_victim) if v is not None]


class Game:
    def __init__(self, guild_id: int, channel_id: int, host_id: int):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.host_id = host_id

        self.players: dict[int, Player] = {}
        self.partner: dict[int, int] = {}          # 共有者・囁き共有者の組の相手(組の人だけが知る)
        self.seats: list[int] = []                 # 席順(開始時にランダムで決める)
        self.custom_composition: dict[str, int] | None = None
        self.mode = Mode.FULL
        self.note: NR.NoteGame | None = None       # ワンナイト人狼の配役・墓地
        self.mode_override: Mode | None = None     # 主催者が選んだモード(None なら人数で自動)
        self.phase = Phase.LOBBY
        self.day = 0

        # 夜
        self.attack_votes: dict[int, int] = {}
        self.guard_actor: int | None = None
        self.guard_target: int | None = None
        self.last_guard_target: int | None = None  # 前の夜の護衛先(連続ガード禁止)
        self.divine: tuple[int, int, str] | None = None
        self.greedy_target: int | None = None      # 強欲な人狼の能力の対象(今夜)
        self.survey: dict[int, int] = {}           # 怪しい人アンケート: 回答者 -> 指名した人
        self.survey_notes: dict[int, str] = {}     # 狂人の一言: 回答者 -> 一言

        # COと報告
        self.co: dict[int, str] = {}                              # いまCOしている役職
        self.fake_co_made: set[int] = set()                       # 一度でも偽COした人
        self.reports: dict[int, list[tuple[int, int, str]]] = {}  # (日, 対象, 結果)
        self.history: list[tuple[int, str]] = []

        # ポイント計算用
        self.seer_found: set[int] = set()
        self.executed_roles: list[str] = []

    # ---------- 参加者 ----------
    def add_player(self, user_id: int, name: str) -> bool:
        if user_id in self.players or len(self.players) >= MAX_PLAYERS:
            return False
        self.players[user_id] = Player(user_id, name)
        return True

    def remove_player(self, user_id: int) -> bool:
        return self.players.pop(user_id, None) is not None

    def alive_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.alive]

    def alive_ids(self) -> list[int]:
        return [p.user_id for p in self.alive_players()]

    def kill(self, user_id: int) -> None:
        self.players[user_id].alive = False

    def mark_quit(self, user_id: int) -> None:
        """突然死(VCから戻らなかった)。以後は死亡扱い。"""
        p = self.players[user_id]
        p.quit_early = True
        p.alive = False

    def mark_left(self, user_id: int) -> None:
        """自分から途中で抜けた(退出)。以後は死亡扱いで、報酬なし・参戦費は返さない。"""
        p = self.players[user_id]
        p.left = True
        p.alive = False

    # ---------- 開始 ----------
    def preview_mode(self) -> Mode:
        if self.mode_override is not None:
            return self.mode_override
        return Mode.FULL if len(self.players) >= FULL_MIN_PLAYERS else Mode.ONE_NIGHT

    @property
    def one_night(self) -> bool:
        return self.mode in (Mode.ONE_NIGHT, Mode.NOTE)

    def composition(self) -> dict[str, int]:
        if self.custom_composition:
            return dict(self.custom_composition)
        return default_composition(len(self.players), self.preview_mode() == Mode.ONE_NIGHT)

    def composition_error(self) -> str | None:
        if self.preview_mode() == Mode.NOTE:
            if len(self.players) > NR.MAX_PLAYERS:
                return f"ワンナイト人狼は{NR.MAX_PLAYERS}人までです(現在{len(self.players)}人)。"
            return None
        return validate_composition(
            self.composition(), len(self.players), self.preview_mode() == Mode.ONE_NIGHT)

    def composition_label(self) -> str:
        if self.preview_mode() == Mode.NOTE:
            return "人狼・占い師1・怪盗1・市民など(人数に合わせた表で配ります。8人は狂人入り)"
        return composition_text(self.composition())

    def assign_note_roles(self) -> None:
        """ワンナイト人狼の配役。余った2枚は墓地に置く。"""
        deal, grave = NR.make_deck(len(self.players))
        order = list(self.players)
        random.shuffle(order)
        for uid, role in zip(order, deal):
            self.players[uid].role = role
            self.players[uid].alive = True
        self.note = NR.NoteGame(roles={u: self.players[u].role for u in order}, graveyard=grave)

    def sync_note_roles(self) -> None:
        """怪盗の交換のあと、プレイヤーの役職を配役に合わせる。"""
        for uid, role in self.note.roles.items():
            self.players[uid].role = role

    def begin(self) -> str | None:
        """開始できるか確認して、モードと席順を決める。問題があればエラーメッセージ。"""
        n = len(self.players)
        if n < MIN_PLAYERS:
            return f"{MIN_PLAYERS}人以上で開始できます(現在{n}人)。"
        err = self.composition_error()
        if err:
            return err
        self.mode = self.preview_mode()
        self.seats = list(self.players)
        random.shuffle(self.seats)
        self.phase = Phase.NIGHT
        self.day = 0
        return None

    def assign_roles(self, hopes: dict[int, tuple[str | None, str | None]]) -> None:
        """希望を見て、抽選で役職を配る。第1希望 → 第2希望 → 残りはランダム。"""
        remaining = Counter(self.composition())
        order = list(self.players)
        random.shuffle(order)
        assigned: dict[int, str] = {}
        for index in (0, 1):
            for uid in order:
                role = hopes.get(uid, (None, None))[index]
                if uid not in assigned and role and remaining[role] > 0:
                    remaining[role] -= 1
                    assigned[uid] = role
        pool = [r for r, c in remaining.items() for _ in range(c)]
        random.shuffle(pool)
        for uid in order:
            if uid not in assigned:
                assigned[uid] = pool.pop()
        for uid, role in assigned.items():
            self.players[uid].role = role
            self.players[uid].alive = True
        self.make_pairs()

    def make_pairs(self) -> None:
        """共有者・囁き共有者を、それぞれ2人1組にする(組は同じ種類どうし。ランダム)。"""
        self.partner = {}
        for role in ("共有者", "囁き共有者"):
            members = [u for u, p in self.players.items() if p.role == role]
            random.shuffle(members)
            for a, b in zip(members[::2], members[1::2]):
                self.partner[a] = b
                self.partner[b] = a

    # ---------- 夜 ----------
    def record_greedy(self, user_id: int, target: int) -> None:
        """強欲な人狼の能力(もう1人を襲撃)。1ゲーム1回。"""
        self.players[user_id].greedy_used = True
        self.greedy_target = target

    def reset_night(self) -> None:
        self.attack_votes = {}
        self.greedy_target = None
        self.guard_actor = None
        self.guard_target = None
        self.divine = None
        self.survey = {}
        self.survey_notes = {}

    def night_actors(self) -> dict[int, str]:
        """夜に行動する生存者 {user_id: 行動}。ワンナイトは占いだけ。"""
        actors = {}
        for p in self.alive_players():
            act = ROLES[p.role].night_action
            if act is None or (self.one_night and act != "divine"):
                continue
            actors[p.user_id] = act
        return actors

    def survey_roles_active(self) -> bool:
        return not self.one_night

    def night_targets(self, user_id: int, act: str) -> list[Player]:
        alive = self.alive_players()
        if act == "attack":
            return [p for p in alive if p.role not in ATTACKERS]
        if act == "guard":
            return [p for p in alive if p.user_id != user_id and p.user_id != self.last_guard_target]
        return [p for p in alive if p.user_id != user_id]

    def record_night_action(self, user_id: int, act: str, target: int) -> str | None:
        """夜の行動を記録する。占いなら結果("人間"/"人狼")を返す。"""
        if act == "attack":
            self.attack_votes[user_id] = target
        elif act == "guard":
            self.guard_actor = user_id
            self.guard_target = target
        elif act == "divine":
            result = ROLES[self.players[target].role].seen_as
            self.divine = (user_id, target, result)
            if result == "人狼":
                self.seer_found.add(target)
            return result
        return None

    def record_survey(self, user_id: int, target: int, note: str = "") -> None:
        self.survey[user_id] = target
        note = note.strip()
        if note:
            self.survey_notes[user_id] = note[:100]

    def survey_counts(self) -> Counter:
        return Counter(self.survey.values())

    def random_white(self, seer_id: int) -> Player | None:
        """初日の占い。人狼でも本人でもない人をランダムに選ぶ。"""
        pool = [p for p in self.alive_players() if p.role not in ATTACKERS and p.user_id != seer_id]
        return random.choice(pool) if pool else None

    def resolve_night(self) -> NightResult:
        """襲撃先を決める。護衛が当たっていれば victim は None。"""
        if self.one_night:                       # 簡略化人狼は、夜が1回だけで、襲撃はしない
            return NightResult(None, self.guard_target, None, self.divine)
        pool = [p.user_id for p in self.alive_players() if p.role not in ATTACKERS]
        attack: int | None = None
        if self.attack_votes:
            counts = Counter(self.attack_votes.values())
            top = max(counts.values())
            attack = random.choice([t for t, c in counts.items() if c == top])  # 同数ならランダム
        elif pool:
            attack = random.choice(pool)
        victim = None if attack is None or attack == self.guard_target else attack
        greedy = self.greedy_target
        greedy_victim = None
        if greedy is not None and greedy != self.guard_target and greedy != victim:
            greedy_victim = greedy
        gj = (attack is not None and attack == self.guard_target) or (greedy is not None and greedy == self.guard_target)
        return NightResult(attack, self.guard_target, victim, self.divine,
                           greedy_target=greedy, greedy_victim=greedy_victim, gj=gj)

    def apply_night(self, res: NightResult) -> None:
        """夜の結果を記録する(襲撃成功・GJ・夜を越えた回数)。死亡の処理は呼び出し側。"""
        if res.victims:
            for p in self.alive_players():
                if p.role in ATTACKERS:
                    p.kills += len(res.victims)
        if res.gj and self.guard_actor is not None:
            self.players[self.guard_actor].gj += 1
        for p in self.alive_players():
            if p.user_id not in res.victims:
                p.nights += 1
        self.last_guard_target = res.guard_target

    # ---------- 投票 ----------
    def vote_order(self, jumped: list[int], done: set[int]) -> int | None:
        """次に投票する人。割り込み(先着)があればその人、なければ席順で次の人。"""
        alive = set(self.alive_ids())
        for uid in jumped:
            if uid in alive and uid not in done:
                return uid
        for uid in self.seats:
            if uid in alive and uid not in done:
                return uid
        return None

    def tally(self, votes: dict[int, int], candidates: list[int]):
        """投票を集計する。戻り値: (得票数, 最多得票者のリスト)。未投票の票は数えない。"""
        counts = Counter(t for v, t in votes.items()
                         if v in self.players and self.players[v].alive and t in candidates and t != v)
        top = max((counts.get(c, 0) for c in candidates), default=0)
        return counts, [c for c in candidates if counts.get(c, 0) == top]

    def peace_available(self) -> bool:
        """「誰も追放しない」が投票に出るか(平和主義者が生きている間)。"""
        return any(p.role == "平和主義者" and p.alive for p in self.players.values())

    def peace_wins(self, counts: Counter, peace_votes: int) -> bool:
        """「誰も追放しない」の票が、いちばん多い人の票より多ければ、処刑なし。同数なら、プレイヤーだけで決める。"""
        return peace_votes > max(counts.values(), default=0)

    def target_name(self, uid: int) -> str:
        return "誰も追放しない" if uid == PEACE_VOTE else self.players[uid].name

    def record_votes(self, counts: Counter) -> None:
        for uid, c in counts.items():
            self.players[uid].votes_received += c

    def record_execution(self, user_id: int) -> None:
        self.executed_roles.append(self.players[user_id].role)

    # ---------- 勝敗 ----------
    def check_winner(self) -> str | None:
        """人狼ゲームの勝敗。"village" / "wolf" / None(続行)。"""
        alive = self.alive_players()
        wolves = sum(1 for p in alive if p.role in ATTACKERS)    # 人狼と大狼
        if wolves == 0:
            return "village"
        if wolves >= len(alive) - wolves:
            return "wolf"
        return None

    def one_night_winner(self, executed: int) -> str:
        return "village" if self.players[executed].role == "人狼" else "wolf"

    # ---------- ポイント ----------
    def role_bonus(self, p: Player, winner: str) -> int:
        b = points.ROLE_BONUS
        total = 0
        if p.role == "狂人":
            if p.user_id in self.fake_co_made:
                total += b["狂人_偽CO"]
            faking_now = self.co.get(p.user_id) not in (None, p.role)
            if winner == "wolf" and faking_now:
                total += b["狂人_騙し切り"]
            if winner == "wolf" and p.votes_received == 0:
                total += b["狂人_無投票勝利"]
        elif p.role == "騎士":
            total += b["騎士_GJ"] * min(p.gj, points.KNIGHT_GJ_LIMIT)
        elif p.role in ATTACKERS:
            total += b["人狼_襲撃成功"] * p.kills
        elif p.role == "占い師":
            total += b["占い師_人狼発見"] * len(self.seer_found)
        elif p.role == "霊媒師" and winner == "village":
            whites = sum(1 for r in self.executed_roles if ROLES[r].seen_as == "人間")
            total += b["霊媒師_白"] * whites
        if self.one_night:
            total = round(total * points.ONE_NIGHT_BONUS_SCALE)
        return total

    def settlement(self, winner: str, payers: set[int]) -> points.Settlement:
        if self.mode == Mode.NOTE and self.note:
            wins = NR.settle_inputs(self.note, winner)
            results = [points.Result(uid=u, team=NR.team_of(r), team_won=wins[u], nights_survived=0,
                                     survived_to_end=False, quit_early=False, role_bonus=0)
                       for u, r in self.note.roles.items()]
            return points.settle(results, payers, points.fee_for(True), points.table_for(True))
        results = []
        for p in self.players.values():
            team = ROLES[p.role].team
            results.append(points.Result(
                uid=p.user_id, team=team, team_won=(team == winner), nights_survived=p.nights,
                survived_to_end=p.alive and not p.quit_early, quit_early=p.quit_early,
                role_bonus=self.role_bonus(p, winner), left=p.left,
                win_mult=points.EXTRA_ROLE_WIN_MULT if (ROLES[p.role].extra and not self.one_night) else 1.0))
        return points.settle(results, payers, points.fee_for(self.one_night), points.table_for(self.one_night))

    # ---------- COの記録 ----------
    def _log(self, text: str) -> None:
        self.history.append((self.day, text))

    def set_co(self, user_id: int, role: str) -> str:
        name = self.players[user_id].name
        if role == "撤回":
            if user_id not in self.co:
                return f"{name} はまだ CO していません。"
            old = self.co.pop(user_id)
            self.reports.pop(user_id, None)
            self._log(f"{name}: {old}COを撤回")
            return f"{name} が {old}CO を撤回しました。"
        if role not in CO_CHOICES:
            return "その役職では CO できません。"
        old = self.co.get(user_id)
        if old == role:
            return f"{name} はすでに {role}CO しています。"
        if role != self.players[user_id].role:
            self.fake_co_made.add(user_id)
        if old is not None:
            self.reports.pop(user_id, None)
        self.co[user_id] = role
        if old is None:
            self._log(f"{name}: {role}CO")
            return f"📣 {name} が **{role}CO** しました。"
        self._log(f"{name}: {old}CO → {role}CO に変更")
        return f"📣 {name} が {old}CO から **{role}CO** に変更しました。"

    def add_report(self, user_id: int, target_id: int, result: str) -> tuple[bool, str]:
        role = self.co.get(user_id)
        if role not in ("占い師", "霊媒師"):
            return False, "先に占い師か霊媒師として CO してください。"
        if target_id not in self.players:
            return False, "その人はゲームの参加者ではありません。"
        if target_id == user_id and role == "占い師":
            return False, "自分を占うことはできません。"
        self.reports.setdefault(user_id, []).append((self.day, target_id, result))
        me, target = self.players[user_id].name, self.players[target_id].name
        kind = "占い" if role == "占い師" else "霊媒"
        self._log(f"{me}({role}CO): {kind}結果 {target} → {RESULT_LABEL[result]}")
        return True, f"📣 {me}({role}CO)の{kind}結果: **{target}** は **{RESULT_LABEL[result]}**"

    def co_board(self) -> str:
        def label(uid: int) -> str:
            p = self.players[uid]
            return ("💀" if not p.alive else "") + p.name

        lines: list[str] = []
        for role in CO_CHOICES:
            holders = [uid for uid, r in self.co.items() if r == role]
            if not holders:
                continue
            lines.append(f"**{role}CO**")
            for uid in holders:
                detail = " / ".join(f"{d}日目 {self.players[t].name}→{RESULT_LABEL[res]}"
                                    for d, t, res in self.reports.get(uid, []))
                lines.append(f"・{label(uid)}" + (f" — {detail}" if detail else ""))
        no_co = [label(uid) for uid in self.players if uid not in self.co]
        lines.append("**記録なし**: " + ("、".join(no_co) if no_co else "なし"))
        return "\n".join(lines)

    def history_text(self) -> str:
        if not self.history:
            return "(COの記録はありません)"
        return "\n".join(f"{d}日目 {t}" if d else f"初日 {t}" for d, t in self.history)

    def role_summary(self) -> str:
        return "\n".join(
            f"{ROLES[p.role].emoji} {p.name} — {p.role}" + ("" if p.alive else " 💀")
            for p in self.players.values())
