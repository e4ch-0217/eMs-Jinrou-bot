"""ポイントの数字と、精算の計算。

精算のルール:
- 各自の取り分(raw)を計算する。
- 配る合計は「払われた参加費の合計 × 95%」まで。超えたら、払った人全員の取り分を同じ割合で減らす。
- 免除者(参加費を払っていない人)の取り分は上限に入れず、そのまま払う(通貨がわずかに増える)。
- 突然死した人は、役職ボーナスと、抜けた夜より前の生存日数だけ受け取る(陣営勝利・生還はなし)。
"""
from __future__ import annotations

from dataclasses import dataclass

START_POINTS = 2000
CAP_RATIO = 0.95

# 人狼ゲーム(6人以上)
FULL_FEE = 100
FULL = {"win_village": 120, "win_wolf": 200, "survive": 20, "per_night": 5}

# ワンナイト(5人以下): 参戦費は人狼ゲームの1/10。勝利報酬は少し低めにして、長い人狼ゲームのほうが稼げるようにする。
# 夜が1回だけなので、生還と生存日数はなし。役職ボーナスは人狼ゲームの1/10。
ONE_NIGHT_FEE = 10
ONE_NIGHT = {"win_village": 8, "win_wolf": 16, "survive": 0, "per_night": 0}
ONE_NIGHT_BONUS_SCALE = 0.1

# 役職ボーナス(人狼ゲーム)
ROLE_BONUS = {
    "狂人_偽CO": 5,          # 1ゲーム1回
    "狂人_騙し切り": 50,     # 偽COのまま人狼陣営が勝利(1ゲーム1回)
    "狂人_無投票勝利": 30,   # 自分に1票も入らず人狼陣営が勝利(1ゲーム1回)
    "騎士_GJ": 65,           # 襲撃を防ぐたび(1ゲーム2回まで)
    "人狼_襲撃成功": 35,     # 襲撃に成功するたび(人狼全員が受け取る)
    "占い師_人狼発見": 30,   # 人狼を見つけるたび
    "霊媒師_白": 20,         # 村人陣営が勝ったときだけ、霊媒結果が白の回数ぶん
}
KNIGHT_GJ_LIMIT = 2

# 追加役職(狂信者・囁き狂人・共有者・囁き共有者・大狼・猫又)の持ち主は、陣営勝利報酬が増える(役職ボーナスの代わり)。
# 人狼ゲームだけ。参戦費の上限の計算には、増えたあとの金額が入る。
EXTRA_ROLE_WIN_MULT = 1.5


def table_for(one_night: bool) -> dict:
    return ONE_NIGHT if one_night else FULL


def fee_for(one_night: bool) -> int:
    return ONE_NIGHT_FEE if one_night else FULL_FEE


@dataclass
class Result:
    """1人分の、ゲーム終了時の記録。"""
    uid: int
    team: str                 # "village" / "wolf"
    team_won: bool
    nights_survived: int      # 夜を越えた回数(突然死なら、抜けるまで)
    survived_to_end: bool     # 最後まで生存したか
    quit_early: bool          # 突然死したか
    role_bonus: int = 0       # 役職ボーナスの合計(計算済み)
    left: bool = False        # 自分から途中で抜けたか(報酬なし・参戦費は返さない)
    win_mult: float = 1.0     # 陣営勝利報酬の倍率(追加役職の持ち主は EXTRA_ROLE_WIN_MULT)


def raw_payout(r: Result, table: dict) -> int:
    if r.left:
        return 0
    total = r.role_bonus + table["per_night"] * r.nights_survived
    if r.survived_to_end and not r.quit_early:
        total += table["survive"]
    if r.team_won and not r.quit_early:
        win = table["win_village"] if r.team == "village" else table["win_wolf"]
        total += round(win * r.win_mult)
    return total


@dataclass
class Settlement:
    final: dict[int, int]     # uid -> 受け取る額
    ratio: float              # 払った人への還元率(1.0なら減らしていない)


def settle(results: list[Result], payers: set[int], fee: int, table: dict) -> Settlement:
    raw = {r.uid: raw_payout(r, table) for r in results}
    cap = fee * len(payers) * CAP_RATIO
    payer_raw = sum(v for uid, v in raw.items() if uid in payers)
    ratio = 1.0 if payer_raw <= cap or payer_raw == 0 else cap / payer_raw
    final = {uid: (round(v * ratio) if uid in payers else v) for uid, v in raw.items()}
    return Settlement(final=final, ratio=ratio)
