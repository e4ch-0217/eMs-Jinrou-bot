"""盟約者のルールのテスト。 `python test_pact.py` で実行できます。"""
from game import Game, Phase
from roles import ATTACKERS, ROLES


def make_game(roles: dict[int, str]) -> Game:
    g = Game(1, 1, 0)
    for uid in roles:
        g.add_player(uid, f"P{uid}")
    g.custom_composition = {}
    for r in roles.values():
        g.custom_composition[r] = g.custom_composition.get(r, 0) + 1
    g.players = {}
    for uid in roles:
        g.add_player(uid, f"P{uid}")
    for uid, r in roles.items():
        g.players[uid].role = r
        g.players[uid].alive = True
    g.phase = Phase.NIGHT
    g.day = 1
    return g


def test_pact_role_defined():
    assert ROLES["盟約者"].extra
    assert ROLES["盟約者"].night_action == "pact"
    assert ROLES["盟約者"].seen_as == "人間"


def test_team_follows_partner_after_pact():
    g = make_game({1: "盟約者", 2: "人狼", 3: "市民", 4: "市民"})
    assert g.team_of(g.players[1]) == "village"          # 盟約がまだなら村人陣営
    g.record_night_action(1, "pact", 2)
    assert g.team_of(g.players[1]) == "wolf"              # 盟約先が人狼なら人狼陣営
    assert g.players[1].pact_partner == 2
    g.record_night_action(1, "pact", 3)
    assert g.team_of(g.players[1]) == "village"           # 選び直すと陣営も変わる


def test_pact_needed_until_partner_dies():
    g = make_game({1: "盟約者", 2: "市民", 3: "人狼", 4: "市民"})
    assert g.pact_needed(g.players[1])
    assert g.night_actors().get(1) == "pact"
    g.record_night_action(1, "pact", 2)
    assert not g.pact_needed(g.players[1])
    assert 1 not in g.night_actors()                     # 盟約先が生きている夜は行動なし
    g.kill(2)
    assert g.team_of(g.players[1]) == "village"          # 死んでも次の夜まで今の陣営(盟約先は村人)
    assert g.pact_needed(g.players[1])
    assert g.night_actors().get(1) == "pact"


def test_first_night_only_pact_actors():
    g = make_game({1: "盟約者", 2: "占い師", 3: "人狼", 4: "市民"})
    assert g.first_night_actors() == {1: "pact"}


def test_pact_partner_cannot_be_self_and_timeout_random_targets():
    g = make_game({1: "盟約者", 2: "市民", 3: "人狼", 4: "市民"})
    targets = [p.user_id for p in g.night_targets(1, "pact")]
    assert 1 not in targets and set(targets) == {2, 3, 4}


def test_pact_counts_on_village_side():
    # 人狼1人 と 盟約者(白の盟約先)1人 と 市民1人: 人狼1 対 村側2 なので続行
    g = make_game({1: "盟約者", 2: "人狼", 3: "市民"})
    g.record_night_action(1, "pact", 3)
    assert g.check_winner() is None
    # 盟約者と人狼だけ: 人狼1 対 村側1 なので人狼の勝ち
    g2 = make_game({1: "盟約者", 2: "人狼"})
    assert g2.check_winner() == "wolf"


def test_wolf_partner_pact_is_not_an_attacker():
    g = make_game({1: "盟約者", 2: "人狼", 3: "市民"})
    g.record_night_action(1, "pact", 2)
    assert g.players[1].role not in ATTACKERS
    assert sum(1 for p in g.alive_players() if p.role in ATTACKERS) == 1


def test_consecutive_guard_default_forbidden_and_setting():
    g = make_game({1: "騎士", 2: "市民", 3: "人狼", 4: "市民"})
    g.last_guard_target = 2
    assert 2 not in [p.user_id for p in g.night_targets(1, "guard")]
    g.allow_consecutive_guard = True
    assert 2 in [p.user_id for p in g.night_targets(1, "guard")]


def test_first_night_divine_setting():
    g = make_game({1: "占い師", 2: "市民", 3: "人狼", 4: "市民"})
    assert "占い師" not in [g.players[u].role for u in g.first_night_actors()]
    g.first_night_divine = "choose"
    assert g.first_night_actors().get(1) == "divine"
    assert g.night_targets(1, "divine")  # 占う相手が選べる


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("OK", t.__name__)
