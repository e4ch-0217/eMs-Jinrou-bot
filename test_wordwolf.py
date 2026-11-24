"""ワードウルフのテスト。 `python test_wordwolf.py` で実行できます。"""
import wordwolf
from game import Game, Mode


def make(n: int) -> Game:
    g = Game(1, 1, 0)
    for uid in range(1, n + 1):
        g.add_player(uid, f"P{uid}")
    g.mode_override = Mode.WORDWOLF
    return g


def test_composition_one_wolf_rest_citizens():
    g = make(5)
    assert g.preview_mode() == Mode.WORDWOLF
    assert g.composition() == {"人狼": 1, "市民": 4}
    assert g.composition_error() is None


def test_needs_three_players():
    assert make(2).composition_error() is not None
    assert make(3).composition_error() is None


def test_topic_pair_is_different_words():
    topics = wordwolf.load_topics()
    key, common, odd = wordwolf.pick_topic(topics, set())
    assert common != odd
    assert key == wordwolf.topic_key((common, odd))


def test_topics_file_has_many_topics():
    assert len(wordwolf.load_topics()) >= 20


def test_used_topics_are_avoided():
    topics = [("A", "B"), ("C", "D"), ("E", "F")]
    used = {wordwolf.topic_key(("A", "B")), wordwolf.topic_key(("C", "D"))}
    for _ in range(20):
        key, _, _ = wordwolf.pick_topic(topics, used)
        assert key == wordwolf.topic_key(("E", "F"))


def test_decide_majority_wolf_out_village_wins():
    assert wordwolf.decide({1: 3, 2: 3, 3: 1}, wolf=3) == (3, "village")


def test_decide_wolf_survives_wolf_wins():
    assert wordwolf.decide({1: 3, 2: 3, 3: 3}, wolf=9) == (3, "wolf")


def test_decide_tie_means_no_execution_wolf_wins():
    assert wordwolf.decide({1: 3, 2: 1}, wolf=1) == (None, "wolf")


def test_decide_no_votes_wolf_wins():
    assert wordwolf.decide({}, wolf=1) == (None, "wolf")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
