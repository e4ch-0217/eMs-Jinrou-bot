"""ルールとポイントのテスト。 `python test_rules.py` で実行できます(Discordは不要)。"""
import random

import points
from game import Game, Mode, Phase
from roles import default_composition, parse_composition, validate_composition


def make_game(n):
    g = Game(1, 1, 0)
    for i in range(n):
        assert g.add_player(i, f"P{i}")
    assert g.begin() is None
    g.assign_roles({})
    return g


# ---------- 役職構成 ----------
def test_default_compositions_are_valid():
    for n in range(3, 21):
        one = n < 6
        comp = default_composition(n, one)
        assert sum(comp.values()) == n, (n, comp)
        assert validate_composition(comp, n, one) is None, (n, comp)


def test_parse_and_validate():
    comp = parse_composition("人狼2 占い師1 騎士1 市民3")
    assert comp == {"人狼": 2, "占い師": 1, "騎士": 1, "市民": 3}
    assert validate_composition(comp, 7, False) is None
    assert validate_composition(comp, 8, False)                      # 人数不一致
    assert validate_composition({"人狼": 1, "騎士": 2}, 3, True)      # ワンナイトで騎士は不可
    assert validate_composition({"人狼": 3, "市民": 3}, 6, False)     # 人狼が多すぎ


def test_mode_switch():
    assert make_game(5).mode == Mode.ONE_NIGHT
    assert make_game(6).mode == Mode.FULL


def test_assign_roles_matches_composition_and_hopes():
    for n in (6, 7, 9):
        for _ in range(100):
            g = Game(1, 1, 0)
            for i in range(n):
                g.add_player(i, f"P{i}")
            g.begin()
            hopes = {i: (random.choice(["人狼", "占い師", "騎士", "市民"]), None) for i in range(n)}
            g.assign_roles(hopes)
            got = {}
            for p in g.players.values():
                got[p.role] = got.get(p.role, 0) + 1
            assert got == {k: v for k, v in g.composition().items() if v}
    # 希望が1人だけなら、必ず通る
    g = Game(1, 1, 0)
    for i in range(7):
        g.add_player(i, f"P{i}")
    g.begin()
    g.assign_roles({3: ("騎士", None)})
    assert g.players[3].role == "騎士"


# ---------- 夜・投票・勝敗 ----------
def play_full(n):
    g = make_game(n)
    for _ in range(60):
        g.day += 1
        alive = g.alive_ids()
        votes = {v: random.choice([c for c in alive if c != v]) for v in alive}
        counts, leaders = g.tally(votes, alive)
        executed = random.choice(leaders)
        g.record_votes(counts)
        g.record_execution(executed)
        g.kill(executed)
        if g.check_winner():
            return g.check_winner()
        g.reset_night()
        for uid, act in g.night_actors().items():
            targets = g.night_targets(uid, act)
            if targets:
                g.record_night_action(uid, act, random.choice(targets).user_id)
        res = g.resolve_night()
        g.apply_night(res)
        if res.victim is not None:
            g.kill(res.victim)
        if g.check_winner():
            return g.check_winner()
    raise AssertionError("ゲームが終わりません")


def test_full_games_finish():
    for n in range(6, 21):
        for _ in range(50):
            assert play_full(n) in ("village", "wolf")


def test_one_night_only_seer_acts():
    g = make_game(5)
    assert all(act == "divine" for act in g.night_actors().values())


def test_guard_blocks_attack_and_counts_gj():
    g = make_game(8)
    wolf = next(p for p in g.players.values() if p.role == "人狼")
    knight = next(p for p in g.players.values() if p.role == "騎士")
    target = next(p for p in g.players.values() if p.role not in ("人狼", "騎士"))
    g.reset_night()
    g.record_night_action(wolf.user_id, "attack", target.user_id)
    g.record_night_action(knight.user_id, "guard", target.user_id)
    res = g.resolve_night()
    assert res.victim is None
    g.apply_night(res)
    assert knight.gj == 1 and wolf.kills == 0


def test_no_consecutive_guard_and_no_self_guard():
    g = make_game(8)
    knight = next(p for p in g.players.values() if p.role == "騎士")
    g.reset_night()
    assert knight.user_id not in [p.user_id for p in g.night_targets(knight.user_id, "guard")]
    target = next(p for p in g.players.values() if p.user_id != knight.user_id)
    g.record_night_action(knight.user_id, "guard", target.user_id)
    g.apply_night(g.resolve_night())
    g.reset_night()
    assert target.user_id not in [p.user_id for p in g.night_targets(knight.user_id, "guard")]


def test_wolf_attack_majority_and_tie_random():
    g = make_game(10)
    wolves = [p for p in g.players.values() if p.role == "人狼"]
    victims = [p for p in g.players.values() if p.role != "人狼"][:2]
    g.reset_night()
    g.record_night_action(wolves[0].user_id, "attack", victims[0].user_id)
    g.record_night_action(wolves[1].user_id, "attack", victims[0].user_id)
    g.record_night_action(wolves[2].user_id, "attack", victims[1].user_id)
    for _ in range(20):
        assert g.resolve_night().attack_target == victims[0].user_id


def test_seer_result_and_madman_is_white():
    g = make_game(8)
    seer = next(p for p in g.players.values() if p.role == "占い師")
    wolf = next(p for p in g.players.values() if p.role == "人狼")
    mad = next(p for p in g.players.values() if p.role == "狂人")
    assert g.record_night_action(seer.user_id, "divine", wolf.user_id) == "人狼"
    assert g.record_night_action(seer.user_id, "divine", mad.user_id) == "人間"
    assert g.seer_found == {wolf.user_id}


def test_vote_order_seats_and_jump():
    g = make_game(6)
    first = g.vote_order([], set())
    assert first == g.seats[0]
    jumper = g.seats[4]
    assert g.vote_order([jumper], set()) == jumper
    assert g.vote_order([jumper], {jumper}) == g.seats[0]       # 割り込みは1回だけ
    assert g.vote_order([], set(g.seats)) is None


def test_tally_ignores_abstain_and_self_vote():
    g = make_game(6)
    ids = g.alive_ids()
    counts, leaders = g.tally({ids[0]: ids[1], ids[1]: ids[0], ids[2]: ids[2]}, ids)
    assert set(leaders) == {ids[0], ids[1]}
    assert sum(counts.values()) == 2


# ---------- COの記録 ----------
def test_co_log_and_board():
    g = make_game(8)
    g.day = 1
    assert "占い師CO" in g.set_co(0, "占い師")
    ok, _ = g.add_report(0, 1, "人狼")
    assert ok
    assert not g.add_report(1, 0, "人間")[0]       # COしていない人は報告できない
    g.day = 2
    g.set_co(0, "霊媒師")                           # 変更は履歴に残る
    assert "霊媒師CO" in g.co_board() and "記録なし" in g.co_board()
    assert len(g.history) == 3


# ---------- ポイント ----------
def find(g, role):
    return next(p for p in g.players.values() if p.role == role)


def test_knight_gj_is_capped():
    g = make_game(6)
    k = find(g, "騎士")
    k.gj = 5
    assert g.role_bonus(k, "village") == points.ROLE_BONUS["騎士_GJ"] * points.KNIGHT_GJ_LIMIT


def test_wolf_kills_all_wolves_paid():
    g = make_game(8)
    for w in [p for p in g.players.values() if p.role == "人狼"]:
        w.kills = 2
        assert g.role_bonus(w, "wolf") == points.ROLE_BONUS["人狼_襲撃成功"] * 2


def test_madman_three_bonuses():
    g = make_game(8)
    mad = find(g, "狂人")
    g.day = 1
    g.set_co(mad.user_id, "占い師")
    b = points.ROLE_BONUS
    assert g.role_bonus(mad, "wolf") == b["狂人_偽CO"] + b["狂人_騙し切り"] + b["狂人_無投票勝利"]
    assert g.role_bonus(mad, "village") == b["狂人_偽CO"]
    mad.votes_received = 1                          # 1票入ったら無投票ボーナスなし
    assert g.role_bonus(mad, "wolf") == b["狂人_偽CO"] + b["狂人_騙し切り"]
    g.set_co(mad.user_id, "撤回")                   # 偽COを撤回していたら、騙し切りなし
    assert g.role_bonus(mad, "wolf") == b["狂人_偽CO"]


def test_madman_truthful_claim_is_not_fake():
    g = make_game(8)
    seer = find(g, "占い師")
    g.set_co(seer.user_id, "占い師")
    assert seer.user_id not in g.fake_co_made


def test_medium_only_when_village_wins():
    g = make_game(9)
    m = find(g, "霊媒師")
    g.record_execution(find(g, "市民").user_id)     # 白
    g.record_execution(find(g, "人狼").user_id)     # 黒
    assert g.role_bonus(m, "village") == points.ROLE_BONUS["霊媒師_白"]
    assert g.role_bonus(m, "wolf") == 0


def test_one_night_bonus_is_scaled_down():
    g = make_game(5)
    seer = find(g, "占い師")
    g.seer_found = {1}
    assert g.role_bonus(seer, "village") == round(points.ROLE_BONUS["占い師_人狼発見"] * 0.1)


def res(uid, team="village", won=False, nights=0, end=False, quit_=False, bonus=0):
    return points.Result(uid, team, won, nights, end, quit_, bonus)


def test_raw_payout():
    assert points.raw_payout(res(1, won=True, nights=3, end=True), points.FULL) == 120 + 20 + 15
    # 突然死: 陣営勝利・生還はなし。役職ボーナスと生存日数だけ
    assert points.raw_payout(res(1, won=True, nights=2, quit_=True, bonus=30), points.FULL) == 30 + 10


def test_cap_applies_only_to_payers():
    payers = {1, 2, 3, 4, 5, 6}
    results = [res(i, won=True, nights=3, end=True, bonus=200) for i in range(1, 7)]
    s = points.settle(results, payers, points.FULL_FEE, points.FULL)
    assert s.ratio < 1
    assert sum(s.final[u] for u in payers) <= points.FULL_FEE * 6 * points.CAP_RATIO + 3


def test_free_players_do_not_reduce_payers():
    payers = {1, 2, 3, 4, 5}
    results = [res(i, won=True, nights=3, end=True, bonus=200) for i in range(1, 7)]
    a = points.settle(results, payers, points.FULL_FEE, points.FULL)
    b = points.settle(results, payers | {6}, points.FULL_FEE, points.FULL)
    assert all(a.final[u] == b.final[u] for u in payers)
    assert a.final[6] == points.raw_payout(results[5], points.FULL)    # 免除者は減らされない


def test_no_scaling_under_cap():
    s = points.settle([res(1)], {1}, points.FULL_FEE, points.FULL)
    assert s.ratio == 1.0 and s.final[1] == 0


def test_settlement_full_game():
    g = make_game(7)
    s = g.settlement("village", set(g.players))
    assert set(s.final) == set(g.players) and s.ratio <= 1.0


def test_left_player_gets_nothing_and_fee_not_refunded():
    t = points.FULL
    stay = points.Result(1, "village", True, 2, True, False, role_bonus=0)
    left = points.Result(2, "village", True, 2, False, False, role_bonus=65, left=True)
    assert points.raw_payout(left, t) == 0
    st = points.settle([stay, left], {1, 2}, 100, t)
    assert st.final[2] == 0
    assert st.final[1] == points.raw_payout(stay, t)


def test_quit_early_keeps_bonus_and_nights_only():
    t = points.FULL
    r = points.Result(1, "village", True, 3, False, True, role_bonus=30)
    assert points.raw_payout(r, t) == 30 + 5 * 3


def test_mark_left_and_madman_note():
    g = make_game(7)
    g.mark_left(0)
    assert g.players[0].left and not g.players[0].alive
    res = {r.uid: r for r in []}
    s = g.settlement("village", {0, 1, 2, 3, 4, 5, 6})
    assert s.final[0] == 0
    g.record_survey(1, 2, "  怪しい  ")
    assert g.survey_notes[1] == "怪しい"
    g.record_survey(3, 2)
    assert 3 not in g.survey_notes


def test_host_can_force_one_night_with_many_players():
    from game import Mode
    g = make_game(10)
    g.mode_override = Mode.ONE_NIGHT
    g.custom_composition = None
    assert g.preview_mode() == Mode.ONE_NIGHT
    assert g.composition_error() is None
    g.mode_override = None
    assert g.preview_mode() == Mode.FULL


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
