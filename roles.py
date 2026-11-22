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
    extra: bool = False       # 拡張役職(通常の人狼ゲームで、主催者が /jinro roles で指定したときだけ使う)


ROLE_LIST = [
    Role("人狼", "wolf", "人狼", "attack", "🐺",
         "夜に1人を襲撃します。人狼の数が村人陣営と同数以下になれば勝ちです。"),
    Role("狂人", "wolf", "人間", None, "🃏",
         "人狼陣営の人間です。人狼が誰かは分かりません。占い・霊媒では「人間」と出ます。"),
    Role("占い師", "village", "人間", "divine", "🔮",
         "夜に1人を占い、「人間」か「人狼」かを知ります。初日は、ランダムな「人間」が1人伝えられます。"),
    Role("霊媒師", "village", "人間", None, "👻",
         "処刑された人が「人間」か「人狼」だったかを知ります(襲撃で死んだ人は対象外)。"),
    Role("騎士", "village", "人間", "guard", "🛡️",
         "夜に1人を襲撃から守ります。自分は守れません。同じ人を2日連続では守れません(設定で連続ガードを許可すると守れます)。"),
    Role("市民", "village", "人間", None, "🏘️",
         "特別な能力はありません。議論と投票で人狼を見つけ出します。"),
    # ---- 拡張役職(基本役職とは別。通常の人狼ゲームだけ) ----
    Role("狂信者", "wolf", "人間", None, "🙏",
         "人狼陣営です。人狼が誰かを全員知っていますが、会話はできません。人狼からは、あなたが誰か分かりません。"
         "占い・霊媒では「人間」と出ます。", extra=True),
    Role("囁き狂人", "wolf", "人間", None, "🗣️",
         "人狼陣営です。人狼が誰かを全員知っていて、人狼からも分かります。夜は人狼の巣で、人狼と相談できます。"
         "占い・霊媒では「人間」と出ます。", extra=True),
    Role("共有者", "village", "人間", None, "🤝",
         "村人陣営です。2人1組で、組の相手(共有者)が誰かだけ知っています。組はゲーム開始時にランダムで決まります。夜の行動はありません。", extra=True),
    Role("囁き共有者", "village", "人間", None, "🔔",
         "村人陣営です。2人1組で、組の相手(囁き共有者)が誰かだけ知っています。夜は、組だけの「共有者の巣」で秘密の相談ができます。", extra=True),
    Role("大狼", "wolf", "人間", "attack", "🌑",
         "人狼と同じように、夜に1人を襲撃します。人狼の数にも数えます。占いでは「人間」と出ます。", extra=True),
    Role("静かな人狼", "wolf", "人狼", "attack", "🤫",
         "人狼と同じように、夜に1人を襲撃します。この人が生きている間に襲撃された人は、遺言ができません。"
         "投票で処刑された人は、遺言ができます。占いでは「人狼」と出ます。", extra=True),
    Role("強欲な人狼", "wolf", "人狼", "attack", "🤑",
         "人狼と同じように、夜に1人を襲撃します。人狼の数にも数えます。占いでは「人狼」と出ます。"
         "1ゲームに1回だけ、夜に「能力を使う」を選ぶと、もう1人を追加で襲撃できます(2人が同時に襲撃されます)。", extra=True),
    Role("平和主義者", "village", "人間", None, "🕊️",
         "村人陣営です。生きている間は、投票に「誰も追放しない」が出ます。「誰も」の票が、いちばん多い人の票より多ければ、その日は誰も処刑されません。", extra=True),
    Role("猫又", "village", "人間", None, "🐱",
         "村人陣営です。襲撃されたときは人狼を1人、処刑されたときは生存者を1人ランダムで道連れにします。", extra=True),
    Role("盟約者", "village", "人間", "pact", "🤝",
         "基本は人間です。初日の夜に生きている人を1人選ぶと、その人(盟約先)と2人だけのチャットができ、盟約先の役職が分かります。"
         "盟約先が白なら市民のように、黒なら狂人のように動きます。盟約先が死んだら、次の夜にまた選び直します。", extra=True),
    Role("賢狼", "wolf", "人狼", "attack", "🧠",
         "人狼の仲間です。夜は、他の人狼と一緒に襲撃先を選びます。襲撃が成功したとき、襲撃された人の役職が、人狼みんなと天国に出ます。占いでは黒です。", extra=True),
    Role("能ある人狼", "wolf", "人狼", "attack", "🦉",
         "人狼の仲間です。能ある人狼が生きている間は、夜に「襲撃しない」を選べます。生存している人狼が全員「襲撃しない」を選んだ夜は、誰も死にません。占いでは黒です。", extra=True),
]

ROLES: dict[str, Role] = {r.name: r for r in ROLE_LIST}
ROLES["怪盗"] = Role("怪盗", "village", "人間", None, "🎭",
                    "夜に、他の人と役職を交換できます(任意)。交換したら、新しい役職になります。")
ROLE_ORDER = [r.name for r in ROLE_LIST]

ONE_NIGHT_ROLES = {"人狼", "狂人", "占い師", "市民"}   # ワンナイトで使える役職(拡張役職は使えない)
ATTACKERS = ("人狼", "大狼", "静かな人狼", "強欲な人狼", "賢狼", "能ある人狼")                           # 夜に襲撃する役職。人狼の数に数える
WOLF_CHAT_ROLES = ("人狼", "大狼", "静かな人狼", "強欲な人狼", "賢狼", "能ある人狼", "囁き狂人")          # 人狼の巣で話せる役職
EXTRA_ROLES = tuple(r.name for r in ROLE_LIST if r.extra)
CO_CHOICES = ["占い師", "霊媒師", "騎士", "人狼", "狂人",                       # COできる役職(人狼陣営の役職は偽COも可)
              "大狼", "静かな人狼", "強欲な人狼", "狂信者", "囁き狂人",
              "共有者", "囁き共有者", "猫又", "平和主義者", "盟約者", "賢狼", "能ある人狼"]
WOLF_SIDE_CO = tuple(r for r in CO_CHOICES if ROLES[r].team == "wolf")   # 人狼陣営の役職。COの前に確認する
SURVEY_ROLES = {"市民", "霊媒師", "狂人", "共有者", "囁き共有者", "猫又", "平和主義者"}   # 夜に「怪しい人アンケート」に答える役職
NOTE_ONLY_ROLES = ("狂信者", "囁き狂人")               # 夜に一言だけ書ける(天国に出る。アンケートなし)


def default_composition(n: int, one_night: bool) -> dict[str, int]:
    """人数に応じた標準の役職構成。"""
    if one_night:
        comp = {"人狼": 1, "占い師": 1}
        if n >= 5:                     # 4人は狂人なし(簡略化人狼の表どおり)
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


def composition_total(comp: dict[str, int]) -> int:
    return sum(comp.values())


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
    for r in ("共有者", "囁き共有者"):
        if comp.get(r, 0) % 2:
            return f"{r}は2人1組なので、人数は2人・4人…の偶数にしてください(今は{comp[r]}人)。"
    wolves = sum(comp.get(r, 0) for r in ATTACKERS)
    if wolves < 1:
        return "人狼(または大狼)が1人もいません。"
    if wolves >= n - wolves:
        return "人狼(大狼を含む)が多すぎます(人狼以外より少なくしてください)。"
    return None
