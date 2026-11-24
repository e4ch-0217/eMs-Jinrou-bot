"""ワードウルフのお題と、投票の結果の判定(Discordに依存しない)。"""
import os
import random
from collections import Counter

TOPICS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wordwolf_topics.txt")

# お題ファイル(wordwolf_topics.txt)が読めないときの予備
DEFAULT_TOPICS = [("ケーキ", "ドーナツ"), ("猫", "犬"), ("りんご", "みかん")]


def load_topics() -> list[tuple[str, str]]:
    """お題ファイルを読む。ゲームを始めるたびに読むので、追加したお題はすぐ使われる。"""
    topics: list[tuple[str, str]] = []
    try:
        with open(TOPICS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [x.strip() for x in line.split(",")]
                if len(parts) == 2 and parts[0] and parts[1] and parts[0] != parts[1]:
                    topics.append((parts[0], parts[1]))
    except OSError:
        pass
    return topics or list(DEFAULT_TOPICS)


def topic_key(pair: tuple[str, str]) -> str:
    """お題を覚えておくための名前(並べ替えても同じになる)。"""
    return "|".join(sorted(pair))


def pick_topic(topics: list[tuple[str, str]], used: set[str]) -> tuple[str, str, str]:
    """(お題の名前, 多くの人のお題, 人狼のお題) を返す。used に入っているお題は避ける。
    全部使い切っていたら、used を無視して選ぶ(呼ぶ側で used をリセットする)。"""
    available = [t for t in topics if topic_key(t) not in used] or topics
    pair = random.choice(available)
    common, odd = pair if random.random() < 0.5 else (pair[1], pair[0])
    return topic_key(pair), common, odd


def decide(votes: dict[int, int], wolf: int) -> tuple[int | None, str]:
    """投票の結果。戻り値は(処刑された人 or None, 勝った陣営)。
    一番多く入った1人が追放される。同数のときは、処刑なしで人狼の勝ち。"""
    counts = Counter(votes.values())
    if not counts:
        return None, "wolf"
    top = max(counts.values())
    tops = [t for t, c in counts.items() if c == top]
    if len(tops) != 1:
        return None, "wolf"
    executed = tops[0]
    return executed, ("village" if executed == wolf else "wolf")
