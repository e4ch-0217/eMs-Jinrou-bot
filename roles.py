"""役職の定義と、人数ごとの役職構成。新しい役職は ROLE_LIST に足していきます。"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    name: str
    team: str                 # "village"(村人陣営) / "wolf"(人狼陣営)
    seen_as: str              # 占い・霊媒での見え方: "人狼" / "人間"
    night_action: str | None  # "attack" / "divine" / "guard" / None
    emoji: str
    description: str


ROLE_LIST = [
    Role("人狼", "wolf", "人狼", "attack", "🐺",
         "夜に1人を襲撃します。人狼の数が村人陣営と同数以下になれば勝ちです。複数いるときは、夜の間だけ作戦会議ができます。"),
    Role("狂人", "wolf", "人間", None, "🃏",
         "人狼陣営の人間です。人狼が誰かは分かりません。占い・霊媒では「人間」と出ます。"),
    Role("占い師", "village", "人間", "divine", "🔮",
         "夜に1人を占い、「人間」か「人狼」かを知ります。初日は、ランダムな「人間」が1人伝えられます。"),
    Role("霊媒師", "village", "人間", None, "👻",
         "処刑された人が「人間」か「人狼」だったかを知ります(襲撃で死んだ人は対象外)。"),
    Role("騎士", "village", "人間", "guard", "🛡️",
         "夜に1人を襲撃から守ります。自分は守れず、同じ人を2日連続では守れません。"),
    Role("市民", "village", "人間", None, "🏘️",
         "特別な能力はありません。議論と投票で人狼を見つけ出します。"),
]

ROLES: dict[str, Role] = {r.name: r for r in ROLE_LIST}
ROLE_ORDER = [r.name for r in ROLE_LIST]

ONE_NIGHT_ROLES = {"人狼", "狂人", "占い師", "市民"}   # ワンナイトで使える役職
CO_CHOICES = ["占い師", "霊媒師", "騎士", "市民"]       # COできる役職
SURVEY_ROLES = {"市民", "霊媒師", "狂人"}               # 夜に「怪しい人アンケート」に答える役職


def default_composition(n: int, one_night: bool) -> dict[str, int]:
    """人数に応じた標準の役職構成。"""
    if one_night:
        comp = {"人狼": 1, "占い師": 1}
        if n >= 4:
            comp["狂人"] = 1
    else:
        wolves = {6: 1, 7: 2, 8: 2, 9: 2, 10: 3, 11: 3, 12: 3}.get(n, (n + 2) // 4)
        comp = {"人狼": wolves, "占い師": 1, "騎士": 1}
        if n == 6 or n >= 8:
            comp["狂人"] = 1
        if n >= 8:
            comp["霊媒師"] = 1
    comp["市民"] = n - sum(comp.values())
    return comp


def composition_text(comp: dict[str, int]) -> str:
    return " / ".join(f"{ROLES[r].emoji}{r}×{comp[r]}" for r in ROLE_ORDER if comp.get(r))


def parse_composition(text: str) -> dict[str, int]:
    """「人狼2 占い師1 騎士1 市民3」のような文字列を役職構成に変換する。"""
    pairs = re.findall(r"([^\s\d:：=＝×x,、]+)\s*[:：=＝×x]?\s*(\d+)", text)
    if not pairs:
        raise ValueError("「人狼2 占い師1 市民3」のように、役職名と人数を並べて入力してください。")
    comp: dict[str, int] = {}
    for name, count in pairs:
        if name not in ROLES:
            raise ValueError(f"「{name}」という役職はありません。使える役職: {'、'.join(ROLE_ORDER)}")
        comp[name] = comp.get(name, 0) + int(count)
    return comp


def validate_composition(comp: dict[str, int], n: int, one_night: bool) -> str | None:
    """問題があればエラーメッセージ、なければ None。"""
    for name in comp:
        if name not in ROLES:
            return f"「{name}」という役職はありません。"
        if one_night and name not in ONE_NIGHT_ROLES:
            return f"ワンナイトでは「{name}」は使えません(使える役職: {'、'.join(sorted(ONE_NIGHT_ROLES))})。"
    total = sum(comp.values())
    if total != n:
        return f"役職の合計({total}人)が参加人数({n}人)と合っていません。"
    wolves = comp.get("人狼", 0)
    if wolves < 1:
        return "人狼が1人もいません。"
    if wolves >= n - wolves:
        return "人狼が多すぎます(人狼以外より少なくしてください)。"
    return None
