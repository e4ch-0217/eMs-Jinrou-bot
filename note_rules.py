"""ワンナイト人狼(PDFのルール)。Discordに依存しない、ルールだけの部分。

- 3〜8人。カードは人数+2枚(7人のみ人数+1枚)。配るのは人数ぶん、余りは墓地(テーブル中央)。
- 内訳: 人数ごとの表(下の COUNTS)。8人だけ狂人を入れる(人狼陣営)。
- 夜: 占い師は1人のカードを見るか、墓地の2枚を両方見る。人狼は仲間を確認するだけ。
  怪盗は、役職を1回だけ交換するか、しない(任意)。
- 投票: 1人1票。同数で1位が複数なら、その人たちが処刑される。全員バラバラなら処刑なし。
- 勝敗: 処刑された人の中に人狼がいれば村人の勝ち、いなければ人狼の勝ち。
  処刑がないときは、プレイヤーに人狼がいなければ全員の勝ち(平和村)、いれば人狼の勝ち。
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

WOLF, VILLAGER, SEER, THIEF, MADMAN = "人狼", "市民", "占い師", "怪盗", "狂人"
MIN_PLAYERS = 3
MAX_PLAYERS = 8
PEACE = -1          # 「平和村を願う」(投票の選択肢。プレイヤーに人狼がいないときだけ出る)

# 人数: (人狼, 占い師, 怪盗, 狂人, 市民)。配る枚数は人数ぶん。
COUNTS = {
    3: (2, 1, 1, 0, 1),
    4: (2, 1, 1, 0, 2),
    5: (2, 1, 1, 0, 3),
    6: (2, 1, 1, 0, 4),
    7: (2, 1, 1, 0, 4),
    8: (3, 1, 1, 1, 4),
}


def card_counts(n: int) -> dict[str, int]:
    wolf, seer, thief, madman, villager = COUNTS[n]
    return {WOLF: wolf, SEER: seer, THIEF: thief, MADMAN: madman, VILLAGER: villager}


def make_deck(n: int, rng: random.Random | None = None) -> tuple[list[str], list[str]]:
    """(プレイヤーに配る役職, 墓地の2枚)を返す。"""
    if not MIN_PLAYERS <= n <= MAX_PLAYERS:
        raise ValueError(f"ワンナイト人狼は{MIN_PLAYERS}〜{MAX_PLAYERS}人です。")
    rng = rng or random.Random()
    cards = [role for role, c in card_counts(n).items() for _ in range(c)]
    rng.shuffle(cards)
    return cards[:n], cards[n:]


@dataclass
class NoteGame:
    roles: dict[int, str]        # プレイヤー -> 役職(夜の交換で変わることがある)
    graveyard: list[str]         # 墓地の2枚(テーブル中央)

    def wolves(self) -> list[int]:
        return [u for u, r in self.roles.items() if r == WOLF]

    def wolf_partners(self, uid: int) -> list[int]:
        """人狼が、仲間の人狼を確認する。"""
        return [u for u in self.wolves() if u != uid]

    def look_player(self, target: int) -> str:
        """占い師が1人のカードを見る。"""
        return self.roles[target]

    def look_graveyard(self) -> list[str]:
        """占い師が墓地の2枚を両方見る。"""
        return list(self.graveyard)

    def swap(self, thief: int, target: int) -> None:
        """怪盗が、相手と役職を交換する。怪盗は相手の役職になり、相手は怪盗になる。"""
        a, b = self.roles[thief], self.roles[target]
        self.roles[thief], self.roles[target] = b, a


def team_of(role: str) -> str:
    return "wolf" if role in (WOLF, MADMAN) else "village"


def resolve_vote(g: NoteGame, votes: dict[int, int]) -> tuple[str, list[int]]:
    """投票の結果。戻り値: (勝った陣営, 処刑される人)。
    勝った陣営は "village" / "wolf" / "all"(平和村=全員の勝ち)。
    votes は {投票した人: 投票先}。"""
    peace_ok = not g.wolves()                           # 平和村は、人狼がいないときだけ選べる
    counts = Counter(t for t in votes.values() if t in g.roles or (t == PEACE and peace_ok))
    top = max(counts.values(), default=0)
    leaders = [t for t, c in counts.items() if c == top]
    if leaders == [PEACE]:                              # 平和村が一番(1人だけ)
        return "all", []
    if top <= 1:                                        # 全員バラバラ: 処刑なし
        return ("wolf" if g.wolves() else "all"), []
    executed = [t for t in leaders if t != PEACE]       # 同数の1位は全員処刑(平和村は処刑しない)
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
