"""ワンナイト人狼(noteのルール)。Discordに依存しない、ルールだけの部分。

- カードは人数+2枚。配るのは人数ぶん、余った2枚は墓地。
- 内訳: 人狼2・占い師1・怪盗1・市民(人数-2)。
- 夜: 占い師は1人を見るか墓地の1枚を見る。人狼は仲間を確認するだけ。怪盗は役職を交換するか、しない。
- 投票: 1人1票。同数の最多は2人とも処刑、票がバラバラなら処刑なし。
- 平和村: 墓地の2枚が両方人狼で、プレイヤーに人狼がいないとき。「平和村」に一番票が多ければ全員の勝ち。
  それ以外は参加費を返す(無効)。
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

WOLF, VILLAGER, SEER, THIEF = "人狼", "市民", "占い師", "怪盗"
PEACE = -1          # 「平和村を願う」(投票の選択肢)
MIN_PLAYERS = 3


def card_counts(n: int) -> dict[str, int]:
    return {WOLF: 2, VILLAGER: n - 2, SEER: 1, THIEF: 1}


def make_deck(n: int, rng: random.Random | None = None) -> tuple[list[str], list[str]]:
    """(プレイヤーに配る役職, 墓地の2枚)を返す。"""
    if n < MIN_PLAYERS:
        raise ValueError("ワンナイト人狼は3人以上です。")
    rng = rng or random.Random()
    cards = [role for role, c in card_counts(n).items() for _ in range(c)]
    rng.shuffle(cards)
    return cards[:n], cards[n:]


@dataclass
class NoteGame:
    roles: dict[int, str]        # プレイヤー -> 役職(夜の交換で変わることがある)
    graveyard: list[str]         # 墓地の2枚

    def wolves(self) -> list[int]:
        return [u for u, r in self.roles.items() if r == WOLF]

    def wolf_partners(self, uid: int) -> list[int]:
        """人狼が、仲間の人狼を確認する。"""
        return [u for u in self.wolves() if u != uid]

    def look_player(self, target: int) -> str:
        """占い師が1人を見る。"""
        return self.roles[target]

    def look_grave(self, index: int) -> str:
        """占い師が墓地の1枚を見る(0 か 1)。"""
        return self.graveyard[index]

    def swap(self, thief: int, target: int) -> None:
        """怪盗が、相手と役職を交換する。怪盗は相手の役職になり、相手は怪盗になる。"""
        a, b = self.roles[thief], self.roles[target]
        self.roles[thief], self.roles[target] = b, a


def team_of(role: str) -> str:
    return "wolf" if role == WOLF else "village"


def resolve_vote(g: NoteGame, votes: dict[int, int]) -> tuple[str, list[int]]:
    """投票の結果。戻り値: (勝った陣営, 処刑される人)。
    勝った陣営は "village" / "wolf" / "all"(平和村の全員勝ち) / "void"(参加費を返す)。
    votes は {投票した人: 投票先}。投票先が PEACE なら「平和村」。"""
    peace_possible = not g.wolves()
    counts = Counter(t for v, t in votes.items() if v in g.roles and (t == PEACE or t in g.roles))
    if peace_possible:
        others = max((c for t, c in counts.items() if t != PEACE), default=0)
        if counts.get(PEACE, 0) > others:
            return "all", []
        return "void", []
    top = max((c for t, c in counts.items() if t != PEACE), default=0)
    if top < 2:
        return "wolf", []                             # バラバラ: 処刑なし、人狼が残っているので人狼の勝ち
    executed = [t for t, c in counts.items() if c == top]   # 同数は全員処刑(2人のとき、いつもの形)
    if any(g.roles[e] == WOLF for e in executed):
        return "village", executed
    return "wolf", executed


def settle_inputs(g: NoteGame, winner: str) -> dict[int, bool]:
    """ポイント計算用: 各プレイヤーが陣営勝利したかどうか。"""
    result = {}
    for u, role in g.roles.items():
        team = team_of(role)
        result[u] = winner == "all" or winner == team
    return result
