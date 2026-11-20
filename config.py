"""時間の設定(秒)。数字を変えたいときは、ここだけ直します。"""

HOPE_SECONDS = 30            # 希望役職の受付
FIRST_NIGHT_SECONDS = 10     # 初日の夜(襲撃なし)
NIGHT_SECONDS = 60           # 夜の行動(人狼の相談もこの間)
RESULT_DELAY = 5             # 夜の結果が出てから夜明けまで
MORNING_SECONDS = 10         # 朝の犠牲者の説明
VICTIM_WILL_SECONDS = 10     # 襲撃された人の遺言(進行に関係ないことのみ)
EXECUTED_WILL_SECONDS = 30   # 処刑された人の遺言(「意見終了」で早く終えられる)

DISCUSSION_PER_PLAYER = 40   # 議論: 残り1人あたり。30秒単位に切り上げる
EXTEND_SECONDS = 60          # 延長1回の長さ
EXTEND_MAX = 2               # 延長は1試合2回まで(時短は無制限)

NOTE_VOTE_SECONDS = 60       # ワンナイト人狼の投票の時間
VOTE_GRACE_SECONDS = 10      # 「投票が始まります」の猶予(ここで1番手に割り込める)
VOTE_CONFIRM_SECONDS = 30    # 自分の番で、投票先を確定するまでの時間
VOTE_SPEECH_SECONDS = 20     # 投票のスピーチ
TIE_SPEECH_SECONDS = 20      # 決選投票に残った人の主張
TIE_VOTE_SECONDS = 30        # 決選投票の時間

QUIT_GRACE_SECONDS = 180     # VCから抜けたまま戻らなければ凸死(3分)
CLEANUP_SECONDS = 60         # 終了後、天国などを削除するまで
