//! 独自方策のテスト。
//!
//! deckgym 内蔵の方策は実戦の相性をほとんど再現できなかったため
//! （`docs/status.md`）、ポケポケ固有の定石を入れた方策を用意する。

use pocket_engine::deck::{Deck, parse_deck};
use pocket_engine::game::{MatchResult, Strategy, play_one_game};
use pocket_engine::matchup::evaluate_matchup;

const FIRE_DECK: &str = "\
Energy: Fire
2 A1 042
2 A1 043
2 A1 044
2 A1 049
2 A1 050
2 A1 223
2 A1 225
2 P-A 001
2 P-A 005
2 P-A 007
";

const WATER_DECK: &str = "\
Energy: Water
2 A1 053
2 A1 054
2 A1 055
2 A1 219
2 A1 220
2 A1 225
2 P-A 001
2 P-A 002
2 P-A 005
2 P-A 007
";

fn fire() -> Deck {
    parse_deck(FIRE_DECK).expect("炎デッキは有効なはず")
}

fn water() -> Deck {
    parse_deck(WATER_DECK).expect("水デッキは有効なはず")
}

#[test]
fn 方策コードを解釈して戻せる() {
    assert_eq!("p".parse::<Strategy>().unwrap(), Strategy::Pocket);
    assert_eq!(Strategy::Pocket.code(), "p");
}

#[test]
fn 同じシードなら同じ結果になる() {
    let (a, b) = (fire(), water());
    let first = play_one_game(&a, &b, Strategy::Pocket, Strategy::Pocket, 42).unwrap();
    let second = play_one_game(&a, &b, Strategy::Pocket, Strategy::Pocket, 42).unwrap();
    assert_eq!(first, second);
}

#[test]
fn 対戦は必ず終了する() {
    let (a, b) = (fire(), water());
    for seed in 0..20 {
        let result = play_one_game(&a, &b, Strategy::Pocket, Strategy::Pocket, seed).unwrap();
        assert_ne!(result, MatchResult::Unfinished, "シード {seed}");
    }
}

#[test]
fn ミラーマッチの勝率は五割付近になる() {
    let matchup = evaluate_matchup(&fire(), &fire(), Strategy::Pocket, Strategy::Pocket, 200, 9)
        .expect("評価できるはず");
    let rate = matchup.overall().win_rate().expect("決着があるはず");
    assert!((0.40..=0.60).contains(&rate), "ミラーが偏っている: {rate}");
}

#[test]
fn ランダムに対して勝ち越す() {
    // 方策の強さの下限。ランダムに勝てないものは方策として意味がない
    let matchup = evaluate_matchup(&fire(), &fire(), Strategy::Pocket, Strategy::Random, 200, 3)
        .expect("評価できるはず");
    let rate = matchup.overall().win_rate().expect("決着があるはず");
    assert!(rate > 0.70, "ランダムに対して勝率が低い: {rate}");
}

#[test]
fn 既存の最良方策に勝ち越す() {
    // deckgym 内蔵で最も速く強い aa（エネルギーを付けて殴る）を上回ること
    let matchup = evaluate_matchup(
        &fire(),
        &fire(),
        Strategy::Pocket,
        Strategy::AttachAttack,
        200,
        5,
    )
    .expect("評価できるはず");
    let rate = matchup.overall().win_rate().expect("決着があるはず");
    assert!(rate > 0.55, "aa に勝ち越せていない: {rate}");
}

/// ツボツボ + ビークイン（B4a のメタデッキ）。撤退の判断を確かめるのに使う。
const SHUCKLE_DECK: &str = "\
2 Combee B4 10
2 Vespiquen ex B4 11
2 Shuckle ex A4 21
1 Teal Mask Ogerpon ex B2 17
2 Professor's Research P-A 7
2 Copycat B1 225
1 Cyrus A2 150
1 Sabrina A1 225
2 X Speed P-A 2
1 Field Blower B3 147
2 Leaf Cape A3 147
2 Fragrant Forest B3 153
Energy: Grass
";

/// ロケット団のラッタ ex + アローラキュウコン ex（B4a のメタデッキ）。
const RATICATE_DECK: &str = "\
2 Alolan Vulpix A3 40
2 Alolan Ninetales ex B2 29
2 Team Rocket's Rattata B4a 58
2 Team Rocket's Raticate ex B4a 59
2 Copycat B1 225
2 Professor's Research P-A 7
1 Sabrina A1 225
1 Cyrus A2 150
2 Poké Ball P-A 5
1 Repel A3a 64
1 Elegant Cape B3b 65
1 Soothing Shore B4 154
1 Training Area B2 153
Energy: Water
";

/// サザンドラ + メガアブソル ex（B4a のメタデッキ）。
const HYDREIGON_DECK: &str = "\
2 Deino B1 155
2 Hydreigon B1 157
1 Bombirdier B3 115
1 Mega Absol ex B1 151
2 Professor's Research P-A 7
2 Copycat B1 225
1 Cyrus A2 150
1 Sabrina A1 225
2 Poké Ball P-A 5
2 Rare Candy A3 144
2 Lucky Ice Pop B2 145
2 Deceptive Needle B4 148
Energy: Darkness
";

/// バタフリー + メガジュカイン（B4a のメタデッキ）。
const BUTTERFREE_DECK: &str = "\
2 Caterpie B3b 1
2 Metapod B3b 2
2 Butterfree B3b 3
1 Treecko B3 5
1 Grovyle B3 6
1 Mega Sceptile ex B3 8
2 Professor's Research P-A 7
1 Erika A1 219
1 Copycat B1 225
1 Sabrina A1 225
1 Cyrus A2 150
2 Quick-Grow Extract B1a 67
1 Leaf Cape A3 147
2 Fragrant Forest B3 153
Energy: Grass
";

#[test]
fn 無傷のバトル場から意味なく逃げない() {
    // ダメージを受けていないポケモンから、より弱いポケモンへ逃げるのは 1 ターンの損。
    // 実際の対戦ログで、残り HP 67% の Shuckle ex から
    // エネルギーのない Teal Mask Ogerpon ex へ逃げていた。
    //
    // 育て終えた主力と入れ替えるための撤退は正しい判断なので、
    // 逃げ先にエネルギーが付いている場合は数えない
    use pocket_engine::players::score_action;
    use pocket_engine::replay::play_one_game_with_log;

    let _ = score_action; // 公開 API であることの確認
    let shuckle = parse_deck(SHUCKLE_DECK).expect("ツボツボデッキは有効なはず");
    let butterfree = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");

    let replay =
        play_one_game_with_log(&shuckle, &butterfree, Strategy::Pocket, Strategy::Pocket, 1)
            .expect("対戦できるはず");

    // 逃げた行動の直前の盤面で、バトル場が半分以上の HP を残していないこと
    let mut reckless = 0;
    for (index, step) in replay.steps.iter().enumerate() {
        if !step.description.contains("にげる") || index == 0 {
            continue;
        }
        let before = &replay.steps[index - 1];
        let Some(active) = before.active[step.actor].as_ref() else {
            continue;
        };
        let ratio = f64::from(active.remaining_hp) / f64::from(active.max_hp);
        if ratio <= 0.5 {
            continue;
        }
        // 逃げ先が攻撃できるなら、入れ替え目的の正しい撤退とみなす
        let destination_can_attack = step.active[step.actor]
            .as_ref()
            .is_some_and(|after| !after.energy.is_empty());
        // まだ撃てない主役（ex）が壁の後ろに下がるのも正しい撤退。
        // `docs/play-principles.md`「撃てない主役は壁と入れ替えて裏で完成させる」
        let hiding_unready_main = active.name.contains(" ex") && active.energy.len() < 2;
        if !destination_can_attack && !hiding_unready_main {
            reckless += 1;
        }
    }
    assert_eq!(reckless, 0, "余裕のある盤面から {reckless} 回逃げている");
}

#[test]
fn メタデッキ同士でも決着する() {
    let shuckle = parse_deck(SHUCKLE_DECK).expect("ツボツボデッキは有効なはず");
    let butterfree = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");
    for seed in 0..10 {
        let result = play_one_game(
            &shuckle,
            &butterfree,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .unwrap();
        assert_ne!(result, MatchResult::Unfinished, "シード {seed}");
    }
}

#[test]
fn 手札が多いときはドローカードを使わない() {
    // 手札上限は 10 枚。上限近くでドローしても引けず、サポートの枠を無駄にする
    use pocket_engine::players::score_trainer;

    let full_hand = score_trainer("Professor's Research", 9, 60, 130, false);
    let empty_hand = score_trainer("Professor's Research", 2, 60, 130, false);
    assert!(
        empty_hand > full_hand,
        "手札が少ないときのほうが高く評価されるべき: 少 {empty_hand} / 多 {full_hand}"
    );
}

#[test]
fn 逃げる予定がなければ逃げる補助を使わない() {
    // X Speed はにげるコストを 1 減らすだけ。逃げないなら手札の無駄
    use pocket_engine::players::score_trainer;

    // バトル場が無傷（逃げる必要がない）
    let healthy = score_trainer("X Speed", 5, 130, 130, false);
    // バトル場が瀕死（逃げたい）
    let hurt = score_trainer("X Speed", 5, 20, 130, false);
    assert!(healthy < 0.0, "無傷なのに使おうとしている: {healthy}");
    assert!(hurt > healthy, "瀕死でも評価が上がらない: {hurt}");
}

#[test]
fn 回復カードはダメージを受けているときだけ使う() {
    use pocket_engine::players::score_trainer;

    let healthy = score_trainer("Potion", 5, 130, 130, false);
    let hurt = score_trainer("Potion", 5, 40, 130, false);
    assert!(healthy < 0.0, "無傷なのに回復しようとしている: {healthy}");
    assert!(hurt > healthy);
}

#[test]
fn ベンチが埋まっていればサーチカードを使わない() {
    use pocket_engine::players::score_trainer;

    let bench_open = score_trainer("Poké Ball", 5, 130, 130, false);
    let bench_full = score_trainer("Poké Ball", 5, 130, 130, true);
    assert!(
        bench_open > bench_full,
        "ベンチが空いているほうが高く評価されるべき"
    );
}

#[test]
fn 未知のトレーナーズも使える評価にする() {
    // 対応表にないカードでも、まったく使わないのは損。控えめな正の値にする
    use pocket_engine::players::score_trainer;

    let unknown = score_trainer("Some Unknown Card", 5, 130, 130, false);
    assert!(
        unknown > 0.0,
        "未知のカードが使われなくなっている: {unknown}"
    );
}

/// メガバシャーモ（B4a のメタデッキ）。2 進化をレアキャンディで短縮するのが基本形。
const ELF_BAZOOKA_DECK: &str = "\
2 Spinarak B1a 5
2 Ariados B1a 6
2 Cottonee B1 15
2 Whimsicott ex B1 16
1 X Speed P-A 2
2 Quick-Grow Extract B1a 67
2 Team Rocket's Goo-zooka B4a 68
1 Leaf Cape A3 147
2 Professor's Research P-A 7
2 Copycat B1 225
2 Fragrant Forest B3 153
Energy: Grass
";

const BLAZIKEN_DECK: &str = "\
2 Torchic B1 33
2 Mega Blaziken ex B1 36
1 Heatmor B1 44
1 Castform Sunny Form B3 24
2 Professor's Research P-A 7
2 Copycat B1 225
1 Cyrus A2 150
2 Flame Patch B1 217
2 Rare Candy A3 144
2 Poké Ball P-A 5
1 Field Blower B3 147
1 Rocky Helmet A2 148
1 Hiking Trail B2b 69
Energy: Fire
";

#[test]
fn 二進化デッキの主役を場に出せる() {
    // 主役が出せないと、デッキの性能ではなく方策の欠陥を測ることになる。
    // aa は 30 試合中 0 回しか Mega Blaziken ex を場に出せていなかった
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(SHUCKLE_DECK).expect("ツボツボデッキは有効なはず");

    let mut appeared = 0;
    let trials = 30usize;
    for seed in 0..trials as u64 {
        let replay = play_one_game_with_log(
            &blaziken,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        let found = replay.steps.iter().any(|step| {
            let on_board = |slot: &Option<pocket_engine::replay::PokemonSnapshot>| {
                slot.as_ref().is_some_and(|p| p.name == "Mega Blaziken ex")
            };
            on_board(&step.active[0]) || step.bench[0].iter().any(|p| p.name == "Mega Blaziken ex")
        });
        appeared += usize::from(found);
    }

    assert!(
        appeared * 2 >= trials,
        "主役が場に出た試合が少なすぎる: {appeared}/{trials}"
    );
}

#[test]
fn レアキャンディを二進化の短縮に使う() {
    use pocket_engine::players::score_trainer;

    // レアキャンディは他のトレーナーズより優先されるべき
    let candy = score_trainer("Rare Candy", 5, 130, 130, false);
    let draw = score_trainer("Professor's Research", 5, 130, 130, false);
    let ball = score_trainer("Poké Ball", 5, 130, 130, false);
    assert!(candy > draw, "ドローより優先されていない");
    assert!(candy > ball, "サーチより優先されていない");
}

#[test]
fn 育て終えた主力と入れ替える() {
    // 2 進化デッキの基本形。序盤は繋ぎのポケモンで耐え、
    // ベンチで育てた主役が完成したらバトル場に出す。
    // 対戦ログではアチャモのまま殴り続けていた
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(SHUCKLE_DECK).expect("ツボツボデッキは有効なはず");

    let mut attacked = 0;
    let trials = 30usize;
    for seed in 0..trials as u64 {
        let replay = play_one_game_with_log(
            &blaziken,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        // 主役がバトル場でワザを使った試合を数える
        let used = replay
            .steps
            .iter()
            .any(|step| step.actor == 0 && step.description.contains("Mega Blaziken ex がワザ"));
        attacked += usize::from(used);
    }

    assert!(
        attacked * 3 >= trials,
        "主役がワザを使った試合が少なすぎる: {attacked}/{trials}"
    );
}

#[test]
fn 相手の打点で倒される盤面を避ける() {
    use pocket_engine::players::survives_next_attack;

    // 残り HP より相手の打点が高ければ倒される
    assert!(!survives_next_attack(30, 60));
    assert!(survives_next_attack(90, 60));
    // ちょうど同じなら倒される
    assert!(!survives_next_attack(60, 60));
}

#[test]
fn 進化系統のたねポケモンをデッキから判定できる() {
    // メガバシャーモ ex は Combusken から進化し、Combusken は Torchic から進化する。
    // デッキに Combusken がなくても（レアキャンディ前提）、Torchic は進化系統のたね
    use pocket_engine::players::evolution_line_basics;

    let deck = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let basics = evolution_line_basics(&deck);
    assert!(
        basics.contains("Torchic"),
        "Torchic が進化系統として判定されていない"
    );
    assert!(!basics.contains("Heatmor"), "Heatmor は進化しない");
    assert!(
        !basics.contains("Castform Sunny Form"),
        "Castform は進化しない"
    );
}

#[test]
fn 初期配置では繋ぎをバトル場に出し主役のたねはベンチに置く() {
    // 動画の定石：序盤はクイタラン・ポアルンで時間を稼ぎ、アチャモは裏で育てる
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(SHUCKLE_DECK).expect("ツボツボデッキは有効なはず");

    let mut violations = 0;
    for seed in 0..30u64 {
        let replay = play_one_game_with_log(
            &blaziken,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        // ターン 0 の自分の配置だけを見る
        let setup: Vec<_> = replay
            .steps
            .iter()
            .filter(|s| s.turn == 0 && s.actor == 0 && s.description.contains("を"))
            .collect();
        let active_is_torchic = setup
            .iter()
            .any(|s| s.description.contains("Torchic をバトル場"));
        let benched_stall = setup.iter().any(|s| {
            (s.description.contains("Heatmor") || s.description.contains("Castform"))
                && s.description.contains("ベンチ")
        });
        if active_is_torchic && benched_stall {
            violations += 1;
        }
    }
    assert_eq!(
        violations, 0,
        "繋ぎがあるのにアチャモを前に出した試合: {violations}"
    );
}

#[test]
fn 引きずり出す対象は今倒せるものを選ぶ() {
    use pocket_engine::players::score_drag_target;

    // 残り HP 30 の通常ポケモンと、残り HP 150 の ex。自分の打点は 60
    let killable = score_drag_target(30, 1.0, 60);
    let tanky_ex = score_drag_target(150, 2.0, 60);
    assert!(
        killable > tanky_ex,
        "倒せる相手より倒せない ex を選んでいる"
    );

    // 両方倒せるなら ex を選ぶ
    let killable_ex = score_drag_target(50, 2.0, 60);
    assert!(killable_ex > killable, "倒せるなら ex を優先すべき");
}

#[test]
fn 相手が残り一点のときはexを前に出さない() {
    use pocket_engine::players::score_promotion;

    // 相手 1 点。ex（2 点）を出すと、倒された時点で 3 点に達して負ける。
    // 通常ポケモン（1 点）なら 2 点止まりで、まだ試合が続く。
    // どちらも相手の次の攻撃で倒される場面
    let ex_forward = score_promotion(120, true, 2.0, 1, false);
    let basic_forward = score_promotion(40, true, 1.0, 1, false);
    assert!(
        basic_forward > ex_forward,
        "負け筋になる ex を前に出している: ex {ex_forward} / 通常 {basic_forward}"
    );

    // 相手 0 点なら打点の高い ex を前に出してよい
    let ex_safe = score_promotion(120, true, 2.0, 0, true);
    let basic_safe = score_promotion(40, true, 1.0, 0, true);
    assert!(ex_safe > basic_safe);
}

#[test]
fn エネルギーは必要な分だけ付ける() {
    use pocket_engine::players::score_attach_target;

    // 引数は (バトル場か, 付いている数, 最小コスト, 最大コスト, 主役か)。
    // 最大コスト 2 のポケモンに既に 2 個付いていれば余剰
    let surplus = score_attach_target(true, 2, 2, 2, false);
    // まだ 0 個なら必要
    let needed = score_attach_target(true, 0, 1, 2, false);
    assert!(needed > surplus, "余剰なのに付けようとしている");

    // 繋ぎのバトル場が攻撃できる状態なら、ベンチの主役を育てるほうを優先する
    let active_ready = score_attach_target(true, 1, 1, 1, false);
    let bench_main = score_attach_target(false, 0, 2, 2, true);
    assert!(
        bench_main > active_ready,
        "繋ぎが攻撃できるのに主役を育てていない"
    );

    // ただしバトル場がまだ一度も攻撃できないなら、そちらが先
    let active_cannot = score_attach_target(true, 0, 1, 1, false);
    assert!(
        active_cannot > bench_main,
        "攻撃できないバトル場を放置している"
    );
}

#[test]
fn エネルギーはあと一個で攻撃できる主役を優先する() {
    use pocket_engine::players::score_attach_target;

    // バトル場の繋ぎはあと 2 個必要（1/3）。ベンチの主役はあと 1 個（0/1）。
    // 3 ターンかけて繋ぎを育てるより、主役を 1 ターンで動かすほうが良い
    let stall_far = score_attach_target(true, 1, 3, 3, false);
    let main_near = score_attach_target(false, 0, 1, 1, true);
    assert!(main_near > stall_far, "3 エネ待ちの繋ぎに付け続けている");

    // バトル場があと 1 個なら、そちらが先
    let stall_near = score_attach_target(true, 0, 1, 1, false);
    assert!(
        stall_near > main_near,
        "あと 1 個で攻撃できるバトル場を放置している"
    );
}

#[test]
fn ワザの効果を評価に含める() {
    use pocket_engine::players::attack_effect_value;

    // ねむり・まひは相手のターンを奪うので、ダメージなしでも価値がある
    let sleep = attack_effect_value(Some("Your opponent's Active Pokémon is now Asleep."), 0);
    assert!(sleep.status_bonus > 0.0, "ねむりに価値がない");
    let none = attack_effect_value(None, 0);
    assert!(
        none.status_bonus.abs() < f64::EPSILON,
        "効果なしに価値が付いている"
    );

    // ベンチ 1 匹につき +30 は、ベンチが 3 匹なら +90
    let harmony = attack_effect_value(
        Some("This attack does 30 more damage for each of your Benched Pokémon."),
        3,
    );
    assert_eq!(harmony.extra_damage, 90);
}

#[test]
fn 初ターンに進化できるたねは前に出す() {
    // イーブイの「進化の加速」はバトル場にいるときだけ働く。
    // 進化系統のたねでも、この特性を持つならバトル場に出すのが正しい
    use pocket_engine::replay::play_one_game_with_log;

    let altaria = parse_deck(ALTARIA_DECK).expect("メガチルタリスデッキは有効なはず");
    let opponent = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");

    let mut eevee_front = 0;
    let mut eevee_available = 0;
    for seed in 0..30u64 {
        let replay = play_one_game_with_log(
            &altaria,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        let setup: Vec<_> = replay
            .steps
            .iter()
            .filter(|s| s.turn == 0 && s.actor == 0)
            .collect();
        // 初期配置でイーブイが出てきた試合だけを数える
        if setup.iter().any(|s| s.description.contains("Eevee を")) {
            eevee_available += 1;
            if setup
                .iter()
                .any(|s| s.description.contains("Eevee をバトル場"))
            {
                eevee_front += 1;
            }
        }
    }
    assert!(
        eevee_available > 0,
        "30 試合でイーブイが一度も初手に来ていない"
    );
    assert_eq!(
        eevee_front, eevee_available,
        "イーブイが初手にあるのに前に出さなかった試合がある: {eevee_front}/{eevee_available}"
    );
}

/// メガチルタリス + エーフィ（B4a のメタデッキ、眠りデッキ）。
const ALTARIA_DECK: &str = "\
2 Swablu B1 196
1 Mega Altaria ex B1 102
2 Eevee B1 184
2 Espeon B3a 20
2 Darkrai B2b 40
1 Igglybuff A4a 59
2 Professor's Research P-A 7
2 Copycat B1 225
1 Sabrina A1 225
2 Poké Ball P-A 5
1 Field Blower B3 147
1 Small Balloon B3b 64
1 Training Area B2 153
Energy: Psychic
";

#[test]
fn 攻撃はほかにやることがなくなってから選ぶ() {
    // ワザを使うとターンが終わる。グッズや展開が残っているのに攻撃すると
    // 手札を使い切れない。人間は必ず攻撃を最後に行う
    use pocket_engine::players::should_attack_now;

    // モンスターボール（90）が残っている → まだ攻撃しない
    assert!(!should_attack_now(Some(90.0)));
    // 残っているのが無駄なカード（負の評価）だけ → 攻撃する
    assert!(should_attack_now(Some(-40.0)));
    // 何も残っていない → 攻撃する
    assert!(should_attack_now(None));
}

#[test]
fn 入れ替えたいときはにげるコスト分のエネルギーに価値がある() {
    use pocket_engine::players::retreat_energy_bonus;

    // 裏の主役が育っていて、バトル場のにげるコスト 2 に対しエネルギー 0 → 付ける価値あり
    assert!(retreat_energy_bonus(true, 0, 2) > 0.0);
    // 既にコスト分ある → 追加の価値なし
    assert!(retreat_energy_bonus(true, 2, 2) <= 0.0);
    // 入れ替える必要がなければ価値なし
    assert!(retreat_energy_bonus(false, 0, 2) <= 0.0);
}

#[test]
fn どうぐは役割に合うポケモンに付ける() {
    use pocket_engine::players::score_tool_target;

    // ふうせん（にげるコスト -1）は、入れ替えたいバトル場のポケモンへ
    let balloon_active = score_tool_target("Small Balloon", true, true, false);
    let balloon_bench = score_tool_target("Small Balloon", false, true, false);
    assert!(
        balloon_active > balloon_bench,
        "ふうせんをベンチに付けている"
    );

    // HP を上げるマント類は主役へ
    let cape_main = score_tool_target("Leaf Cape", false, false, true);
    let cape_other = score_tool_target("Leaf Cape", false, false, false);
    assert!(cape_main > cape_other, "マントを主役以外に付けている");
}

#[test]
fn ナツメは相手のバトル場に投資があるときだけ価値がある() {
    use pocket_engine::players::score_trainer;

    // 一律の高評価をやめる。倒せる対象を選べるサイファーより低くする
    let sabrina = score_trainer("Sabrina", 5, 130, 130, false);
    let cyrus = score_trainer("Cyrus", 5, 130, 130, false);
    assert!(sabrina < cyrus, "ナツメをサイファーと同じ価値にしている");
    assert!(sabrina < 60.0, "ナツメの基礎価値が高すぎる: {sabrina}");
}

#[test]
fn 負け筋の減点は次の攻撃で倒される場合だけ効く() {
    use pocket_engine::players::score_promotion;

    // 相手 1 点。メガシンカ ex（3 点）でも、相手の打点に耐えるなら前に出してよい
    let mega_survives = score_promotion(0, true, 3.0, 1, true);
    let basic_dies = score_promotion(0, false, 1.0, 1, false);
    assert!(
        mega_survives > basic_dies,
        "耐えられるメガを避けて、倒される通常ポケモンを出している"
    );

    // 耐えられないなら減点が効く
    let mega_dies = score_promotion(0, true, 3.0, 1, false);
    assert!(mega_dies < basic_dies, "倒されて負けるメガを前に出している");
}

#[test]
fn ベンチの主役が同じ状況なら打点の高いほうにエネルギーを付ける() {
    use pocket_engine::players::bench_attach_tiebreak;

    // メガハーモニー（基礎 40）とシング（0）。あと 1 個で動くのは同じ
    assert!(bench_attach_tiebreak(40, 1) > bench_attach_tiebreak(0, 0));
    // 進化済みは同じ打点でも優先
    assert!(bench_attach_tiebreak(40, 1) > bench_attach_tiebreak(40, 0));
}

#[test]
fn ワザのコストはタイプ付きで照合する() {
    use deckgym::models::EnergyType::{Colorless, Grass, Water};
    use pocket_engine::players::energy_missing;

    // 草・草に対して草・水 → 草があと 1 個必要。個数だけ見れば 0 だが誤り
    assert_eq!(energy_missing(&[Grass, Grass], &[Grass, Water]), 1);
    assert_eq!(energy_missing(&[Grass, Grass], &[Grass, Grass]), 0);
    // 水・無色に対して草 1 個 → 草は無色分に充てられ、水があと 1 個
    assert_eq!(energy_missing(&[Water, Colorless], &[Grass]), 1);
    // 無色 1 個に対して何もなし → 1 個
    assert_eq!(energy_missing(&[Colorless], &[]), 1);
    // 余っていても足りない分は 0
    assert_eq!(energy_missing(&[Colorless], &[Water, Water]), 0);
}

#[test]
fn 役に立たないタイプのエネルギーは付けない() {
    use deckgym::models::EnergyType::{Colorless, Grass, Water};
    use pocket_engine::players::energy_is_useful;

    // メガジュカイン（草・草）に草が 1 個付いている。水を足しても攻撃に近づかない
    let sceptile = vec![vec![Grass, Grass]];
    assert!(
        !energy_is_useful(&sceptile, &[Grass], Water),
        "水を付けようとしている"
    );
    assert!(energy_is_useful(&sceptile, &[Grass], Grass));

    // 無色コストのポケモンにはどのタイプでも役に立つ
    let furfrou = vec![vec![Colorless]];
    assert!(energy_is_useful(&furfrou, &[], Water));

    // ゲッコウガ（水・無色）に草 1 個 → 水を足せば攻撃できる
    let greninja = vec![vec![Water, Colorless]];
    assert!(energy_is_useful(&greninja, &[Grass], Water));
}

#[test]
fn 入れ替えの判断はこの番に付ければ撃てる打点で比べる() {
    use deckgym::models::EnergyType::Fire;
    use pocket_engine::players::attainable_damage;

    // メガバーニング（炎・炎、120）。炎 1 個なら、この番に 1 個付ければ撃てる
    let mega = vec![(vec![Fire, Fire], 120u32)];
    assert_eq!(attainable_damage(&mega, &[Fire]), 120);
    // 0 個なら 2 個足りないので、この番には撃てない
    assert_eq!(attainable_damage(&mega, &[]), 0);
}

#[test]
fn 一個足りない主役をポアルンと入れ替えない() {
    // 対戦ログ：E1 のメガバシャーモを、毎ターン E1 のポアルン（30 ダメージ）と入れ替えていた
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(SHUCKLE_DECK).expect("ツボツボデッキは有効なはず");

    let mut bad_retreats = 0;
    for seed in 0..30u64 {
        let replay = play_one_game_with_log(
            &blaziken,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        for (index, step) in replay.steps.iter().enumerate() {
            if index == 0 || step.actor != 0 || !step.description.contains("にげる") {
                continue;
            }
            let Some(before) = replay.steps[index - 1].active[0].as_ref() else {
                continue;
            };
            // 余裕のある（残り HP 半分以上）メガバシャーモが、エネルギー付きで逃げるのは誤り
            let healthy = f64::from(before.remaining_hp) / f64::from(before.max_hp) > 0.5;
            if before.name == "Mega Blaziken ex" && !before.energy.is_empty() && healthy {
                bad_retreats += 1;
            }
        }
    }
    assert_eq!(
        bad_retreats, 0,
        "準備できた主役を逃がした回数: {bad_retreats}"
    );
}

#[test]
fn 前に出した主役へのエネルギーは裏のたねより優先する() {
    use pocket_engine::players::score_attach_target;

    // バトル場の主役（メガルカリオ、あと 2 個）と、ベンチのたね（リオル、あと 1 個）
    let active_main = score_attach_target(true, 0, 2, 2, true);
    let bench_basic = score_attach_target(false, 0, 1, 1, true);
    assert!(
        active_main > bench_basic,
        "前に立てた主役を放置して裏のたねに付けている: 主役 {active_main} / たね {bench_basic}"
    );
}

#[test]
fn ナツメは相手のバトル場に投資がなければ使わない() {
    use pocket_engine::players::score_sabrina;

    // 対戦ログ：エネルギー 0 のメガルカリオにナツメを使っていた
    assert!(
        score_sabrina(0) <= 0.0,
        "投資のない相手にナツメを使おうとしている"
    );
    assert!(score_sabrina(2) > 0.0);
    assert!(score_sabrina(2) > score_sabrina(1));
}

#[test]
fn 裏の進化済み主役があと二個なら前の繋ぎより優先する() {
    use pocket_engine::players::{bench_attach_tiebreak, score_attach_target};

    // 前の繋ぎ（アチャモ、あと 1 個で 20 ダメージ）と、裏のメガバシャーモ（あと 2 個で 120）。
    // 繋ぎを 1 ターン早く動かすより、主役を 1 ターン早く動かすほうが良い
    let stall_front = score_attach_target(true, 0, 1, 1, false);
    let mega_bench = score_attach_target(false, 0, 2, 2, true) + bench_attach_tiebreak(120, 2);
    assert!(
        mega_bench > stall_front,
        "裏のメガを後回しにしている: 繋ぎ {stall_front} / メガ {mega_bench}"
    );

    // 裏がたね（リオル、あと 1 個で 40）なら前の繋ぎが先
    let basic_bench = score_attach_target(false, 0, 1, 1, true) + bench_attach_tiebreak(40, 0);
    assert!(stall_front > basic_bench);
}

#[test]
fn 弱点を突かれる高得点のポケモンは場に出すのを控える() {
    use pocket_engine::players::exposure_penalty;

    // メガアブソル ex（3 点、弱点：草）を、草の攻撃者がいる相手に出す
    assert!(exposure_penalty(3.0, true) > 0.0);
    // 通常ポケモン（1 点）なら控えない
    assert!(exposure_penalty(1.0, true) <= 0.0);
    // 弱点を突かれないなら控えない
    assert!(exposure_penalty(3.0, false) <= 0.0);
}

#[test]
fn 自傷する特性は倒れる直前には使わない() {
    use pocket_engine::players::{ability_self_damage, score_self_damaging_ability};

    let roar = "Once during your turn, you may take 2 [D] Energy from your Energy Zone and attach it to this Pokémon. If you do, do 30 damage to this Pokémon.";
    assert_eq!(ability_self_damage(Some(roar)), 30);
    assert_eq!(
        ability_self_damage(Some(
            "Once during your turn, you may do 20 damage to 1 of your opponent's Pokémon."
        )),
        0
    );
    assert_eq!(ability_self_damage(None), 0);

    // 残り HP 40 で 30 の自傷 → 使ってよい。残り 30 → 倒れるので使わない
    assert!(score_self_damaging_ability(40, 30, true, true) > 0.0);
    assert!(score_self_damaging_ability(30, 30, true, true) < 0.0);
    // 自傷なしなら通常の価値
    assert!(score_self_damaging_ability(40, 0, false, false) > 0.0);
}

/// メガルカリオ + ルカリオ（B4a のメタデッキ）。リオルは 1 進化でメガルカリオになる。
const LUCARIO_DECK: &str = "\
2 Riolu A2 91
2 Mega Lucario ex B3 81
1 Lucario A2 92
1 Hitmonchan A1 155
1 Hitmonlee A1 154
2 Professor's Research P-A 7
2 Copycat B1 225
1 Pokémon Center Lady A2b 70
1 Cyrus A2 150
1 Korrina B3 149
2 Poké Ball P-A 5
1 X Speed P-A 2
1 Lucky Ice Pop B2 145
1 Giant Cape A2 147
1 Arena of Antiquity B3 154
Energy: Fighting
";

#[test]
fn 進化系統の段数をデッキから判定できる() {
    use pocket_engine::players::evolution_line_top_stage;

    // アチャモ → メガバシャーモ ex は 2 進化（レアキャンディで短縮）
    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    assert_eq!(evolution_line_top_stage(&blaziken).get("Torchic"), Some(&2));
    // リオル → メガルカリオ ex は 1 進化
    let lucario = parse_deck(LUCARIO_DECK).expect("メガルカリオデッキは有効なはず");
    assert_eq!(evolution_line_top_stage(&lucario).get("Riolu"), Some(&1));
}

#[test]
fn 一進化のたねは前に出して次の番に進化させる() {
    // 2 進化系（アチャモ）は裏で育てるが、1 進化系（リオル）は前に出してすぐ進化させる。
    // 対戦ログ：リオルをベンチに置き、メガルカリオが 30 試合中 16 試合で一度も攻撃しなかった
    use pocket_engine::replay::play_one_game_with_log;

    let lucario = parse_deck(LUCARIO_DECK).expect("メガルカリオデッキは有効なはず");
    let opponent = parse_deck(SHUCKLE_DECK).expect("ツボツボデッキは有効なはず");

    let mut riolu_available = 0;
    let mut riolu_front = 0;
    for seed in 0..30u64 {
        let replay = play_one_game_with_log(
            &lucario,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        let setup: Vec<_> = replay
            .steps
            .iter()
            .filter(|s| s.turn == 0 && s.actor == 0)
            .collect();
        if setup.iter().any(|s| s.description.contains("Riolu を")) {
            riolu_available += 1;
            riolu_front += usize::from(
                setup
                    .iter()
                    .any(|s| s.description.contains("Riolu をバトル場")),
            );
        }
    }
    assert!(riolu_available > 0);
    assert_eq!(
        riolu_front, riolu_available,
        "リオルが初手にあるのに前に出さなかった試合がある: {riolu_front}/{riolu_available}"
    );
}

#[test]
fn 進化はバトル場のポケモンを優先する() {
    use pocket_engine::players::evolve_target_bonus;

    // 同じ進化先なら、バトル場（すぐ殴れる）を優先し、エネルギーが多いほうを優先する
    assert!(evolve_target_bonus(0, 80, 1) > evolve_target_bonus(2, 80, 1));
    assert!(evolve_target_bonus(2, 80, 2) > evolve_target_bonus(2, 80, 0));
}

#[test]
fn 進化して耐えられるなら逃げずに進化する() {
    use pocket_engine::players::evolving_saves_active;

    // リオル（80、残り 30）→ メガルカリオ ex（190）。受けたダメージ 50 を引いても 140 残り、
    // 相手の 30 に耐える
    assert!(evolving_saves_active(190, 50, 30));
    // 進化しても耐えられない
    assert!(!evolving_saves_active(90, 70, 30));
}

#[test]
fn 進化パーツの不足はレアキャンディも見る() {
    use pocket_engine::players::evolution_parts_missing;

    // 引数は (系統の最終段数, 次の段が手札にある, 最終進化が手札にある, レアキャンディが手札にある)
    // 2 進化系：メガが手札にあってもキャンディがなければ不足
    assert!(evolution_parts_missing(2, false, true, false));
    // メガとキャンディが揃えば足りている
    assert!(!evolution_parts_missing(2, false, true, true));
    // 次の段（1 進化）が手札にあれば足りている
    assert!(!evolution_parts_missing(2, true, false, false));
    // 1 進化系：進化先が手札になければ不足
    assert!(evolution_parts_missing(1, false, false, false));
    assert!(!evolution_parts_missing(1, true, false, false));
}

#[test]
fn 零コストで相手を眠らせるたねは前に出す() {
    // 動画：イーブイがなければププリンを前に出し、すやすやソング（0 エネ）で眠らせて時間を稼ぐ。
    // 0 コストのワザは最初の番から使えるので、繋ぎとして最も価値が高い
    use pocket_engine::players::setup_zero_cost_bonus;

    // 0 コストでねむりを与えるワザ → 大きな加点
    assert!(setup_zero_cost_bonus(true, true) > setup_zero_cost_bonus(true, false));
    // 0 コストのワザ自体に加点（最初の番から動ける）
    assert!(setup_zero_cost_bonus(true, false) > 0.0);
    // コストがあれば加点なし
    assert!(setup_zero_cost_bonus(false, true) <= 0.0);
}

#[test]
fn イーブイがなければププリンを前に出す() {
    use pocket_engine::replay::play_one_game_with_log;

    let altaria = parse_deck(ALTARIA_DECK).expect("メガチルタリスデッキは有効なはず");
    let opponent = parse_deck(LUCARIO_DECK).expect("メガルカリオデッキは有効なはず");

    let mut igglybuff_no_eevee = 0;
    let mut igglybuff_front = 0;
    for seed in 0..40u64 {
        let replay = play_one_game_with_log(
            &altaria,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        let setup: Vec<_> = replay
            .steps
            .iter()
            .filter(|s| s.turn == 0 && s.actor == 0)
            .collect();
        let has_eevee = setup.iter().any(|s| s.description.contains("Eevee を"));
        let has_igglybuff = setup.iter().any(|s| s.description.contains("Igglybuff を"));
        if has_igglybuff && !has_eevee {
            igglybuff_no_eevee += 1;
            igglybuff_front += usize::from(
                setup
                    .iter()
                    .any(|s| s.description.contains("Igglybuff をバトル場")),
            );
        }
    }
    assert!(
        igglybuff_no_eevee > 0,
        "40 試合でイーブイなしのププリン初手が一度もない"
    );
    assert_eq!(
        igglybuff_front, igglybuff_no_eevee,
        "ププリンがあるのに前に出さなかった試合がある: {igglybuff_front}/{igglybuff_no_eevee}"
    );
}

#[test]
fn 前に出すポケモンは残り体力が多いほうを優先する() {
    // 動画：相手が眠りから覚めたときの返しに備え、フルヘルスのポケモンを前に出す
    use pocket_engine::players::{Promotion, score_promotion_with_hp};

    let base = Promotion {
        ready_damage: 0,
        has_energy: true,
        points_if_ko: 1.0,
        opponent_points: 0,
        survives: true,
        remaining_hp: 100,
        max_hp: 100,
        can_retreat: true,
        ability_needs_active: false,
    };
    let healthy = score_promotion_with_hp(base);
    let hurt = score_promotion_with_hp(Promotion {
        remaining_hp: 30,
        ..base
    });
    assert!(healthy > hurt, "傷ついたポケモンを優先している");
}

/// ロケット団のマタドガス ex + フーパ ex（B4a のメタデッキ）。
const WEEZING_DECK: &str = "\
2 Hoopa ex B4 103
2 Team Rocket's Koffing B4a 42
2 Team Rocket's Weezing ex B4a 43
1 Darkrai ex A2 110
2 Professor's Research P-A 7
2 Cyrus A2 150
2 Copycat B1 225
1 Mars A2 155
2 Poké Ball P-A 5
1 X Speed P-A 2
1 Field Blower B3 147
2 Deceptive Needle B4 148
Energy: Darkness
";

#[test]
fn してもよい特性は使う() {
    // 動画：マタドガス ex をベンチに置き、特性「ボイラースモック」で相手をどく・やけどにする。
    // deckgym は進化直後に「特性を使う／使わない（Noop）」の選択肢を積む。
    // 同点で後ろ（Noop）を選び、33 回の進化で一度も特性を使っていなかった
    use pocket_engine::replay::play_one_game_with_log;

    let weezing = parse_deck(WEEZING_DECK).expect("マタドガスデッキは有効なはず");
    let opponent = parse_deck(LUCARIO_DECK).expect("メガルカリオデッキは有効なはず");

    let mut evolutions = 0;
    let mut applied = 0;
    for seed in 0..30u64 {
        let replay = play_one_game_with_log(
            &weezing,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        for (index, step) in replay.steps.iter().enumerate() {
            if step.actor != 0 || !step.description.contains("Weezing ex に進化") {
                continue;
            }
            // 相手のバトル場がいなければ対象がない
            if step.active[1].is_none() {
                continue;
            }
            evolutions += 1;
            // 直後の数手のうちに相手がどく・やけどになっているか
            let poisoned = replay.steps[index..(index + 3).min(replay.steps.len())]
                .iter()
                .any(|s| {
                    s.active[1]
                        .as_ref()
                        .is_some_and(|o| o.status.iter().any(|st| st == "どく"))
                });
            applied += usize::from(poisoned);
        }
    }
    assert!(evolutions > 0);
    assert!(
        applied * 10 >= evolutions * 8,
        "特性を使わずに辞退している: {applied}/{evolutions}"
    );
}

#[test]
fn バトル場で自動進化する特性を判定できる() {
    use pocket_engine::players::is_active_self_evolver_effect;

    // キャタピーの「Quick Growth」：バトル場にいると相手の番の終わりに山札から進化
    assert!(is_active_self_evolver_effect(Some(
        "At the end of your opponent's turn, if this Pokémon is in the Active Spot, put a random card from your deck that evolves from this Pokémon onto this Pokémon to evolve it."
    )));
    // イーブイの「進化の加速」も同じ扱い
    assert!(is_active_self_evolver_effect(Some(
        "As long as this Pokémon is in the Active Spot, it can evolve during your first turn or the turn you play it."
    )));
    assert!(!is_active_self_evolver_effect(Some(
        "Once during your turn, you may do 20 damage to 1 of your opponent's Pokémon."
    )));
    assert!(!is_active_self_evolver_effect(None));
}

#[test]
fn キャタピーは初手にあれば前に出す() {
    // ユーザーの指摘：キャタピーはバトル場にいると相手のターン完了後に自動進化する。
    // 以前は 2 進化系のたね同士で HP の高いキモリを前に出していた（12/30）
    use pocket_engine::replay::play_one_game_with_log;

    let butterfree = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");
    let opponent = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");

    let mut available = 0;
    let mut front = 0;
    for seed in 0..30u64 {
        let replay = play_one_game_with_log(
            &butterfree,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        let setup: Vec<_> = replay
            .steps
            .iter()
            .filter(|s| s.turn == 0 && s.actor == 0)
            .collect();
        if setup.iter().any(|s| s.description.contains("Caterpie を")) {
            available += 1;
            front += usize::from(
                setup
                    .iter()
                    .any(|s| s.description.contains("Caterpie をバトル場")),
            );
        }
    }
    assert!(available > 0);
    assert_eq!(
        front, available,
        "キャタピーが初手にあるのに前に出さなかった: {front}/{available}"
    );
}

#[test]
fn 回復は実際に減る分だけ価値がある() {
    // シード 4 のログで、満タンのキモリにエリカ（50 回復）を使っていた。
    // 回復先は deckgym の列挙順で先頭を選んでいたため
    use pocket_engine::players::score_heal;

    assert!(
        score_heal(60, 60, 50, false) < 1.0,
        "満タンへの回復は「何もしない」より低い"
    );
    assert!(score_heal(40, 80, 50, true) > score_heal(60, 80, 50, true));
    // 50 回復でも 20 しか減っていなければ 20 分の価値
    assert!(score_heal(60, 80, 50, true) < score_heal(30, 80, 50, true));
    // 同じ回復量ならバトル場を優先
    assert!(score_heal(40, 80, 50, true) > score_heal(40, 80, 50, false));
}

#[test]
fn 満タンのポケモンを回復先に選ばない() {
    use pocket_engine::replay::play_one_game_with_log;

    let butterfree = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");
    let opponent = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let mut heals = 0;
    for seed in 0..40u64 {
        let replay = play_one_game_with_log(
            &butterfree,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        for (index, step) in replay.steps.iter().enumerate().skip(1) {
            if !step.description.contains("の HP を") {
                continue;
            }
            heals += 1;
            // 回復前の盤面で、回復先が満タンではないことを確かめる
            let before = &replay.steps[index - 1];
            let all: Vec<_> = before.active[step.actor]
                .iter()
                .chain(before.bench[step.actor].iter())
                .collect();
            let full_only = all.iter().all(|p| p.remaining_hp == p.max_hp);
            assert!(
                !full_only,
                "シード {seed}: ダメージを受けたポケモンがいないのに回復した: {}",
                step.description
            );
            // 「効果: 自分のバトル場の Mega Sceptile ex の HP を 50 回復」から名前を取る
            let healed_name = step
                .description
                .split(" の HP")
                .next()
                .and_then(|s| s.rsplit("の ").next())
                .unwrap_or("");
            let target_damaged = all
                .iter()
                .any(|p| p.name == healed_name && p.remaining_hp < p.max_hp);
            assert!(
                target_damaged,
                "シード {seed}: 満タンの {healed_name} を回復した: {}",
                step.description
            );
        }
    }
    assert!(heals > 0, "回復が一度も起きていない");
}

#[test]
fn 逃げ先の負け筋は次の攻撃で倒される場合だけ減点する() {
    // 対戦ログ（バシャーモ先攻 7 ターン目）：相手 1 点の場面で、E1 のメガバシャーモ（210 HP、
    // 相手の打点 60）ではなく E0 のアチャモを逃げ先に選び、主役を温存していた。
    // きぜつ後の昇格（score_promotion）は耐えられるなら減点しないので、逃げ先も揃える
    use pocket_engine::players::score_retreat_destination;

    let mega_survives = score_retreat_destination(1, true, true);
    let torchic = score_retreat_destination(0, false, true);
    assert!(
        mega_survives > torchic,
        "耐えられる主役より E0 のたねを選んだ"
    );

    let mega_dies = score_retreat_destination(1, true, false);
    assert!(mega_dies < torchic, "倒されて負けるなら前に出さない");
}

#[test]
fn 耐えられる主役を温存しない() {
    // 相手が 1 点で、主役（メガ ex、3 点）が相手の打点に耐えられるなら前に出す
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");

    let mut hoarded = 0;
    let mut swaps = 0;
    for seed in 0..40u64 {
        let replay = play_one_game_with_log(
            &blaziken,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        for (index, step) in replay.steps.iter().enumerate() {
            if index == 0 || step.actor != 0 || !step.description.contains("にげる") {
                continue;
            }
            let before = &replay.steps[index - 1];
            // ベンチに撃てる（E2 以上の）メガバシャーモがいるのに、別のたねへ逃げた
            // （ユーザーの指摘の場面は E2。E1 は前に出しても撃てないので対象外）
            let mega_ready = before.bench[0]
                .iter()
                .any(|p| p.name == "Mega Blaziken ex" && p.energy.len() >= 2);
            let went_to_mega = step.active[0]
                .as_ref()
                .is_some_and(|p| p.name == "Mega Blaziken ex");
            if mega_ready {
                swaps += 1;
                if !went_to_mega {
                    hoarded += 1;
                }
            }
        }
    }
    assert!(
        swaps > 0,
        "メガバシャーモが育った状態での入れ替えが一度もない"
    );
    assert_eq!(
        hoarded, 0,
        "育ったメガバシャーモを温存した: {hoarded}/{swaps}"
    );
}

#[test]
fn やけどとどくのダメージを倒せるかの判定に数える() {
    // ユーザーの指摘（バシャーモ先攻 7 ターン目）：メガジュカイン 160 HP に対して
    // メガバーニングは弱点込み 140 + やけど 20 で倒せるのに、倒せないと見ていた。
    // やけど 20・どく 10 は自分の番の終わりのポケモンチェックで入る
    use pocket_engine::players::damage_with_status;

    assert_eq!(damage_with_status(140, true, false), 160);
    // どくは 10。両方なら 30
    assert_eq!(damage_with_status(100, false, true), 110);
    assert_eq!(damage_with_status(100, true, true), 130);
    assert_eq!(damage_with_status(0, false, false), 0);
}

#[test]
fn 今のバトル場を倒せるなら引きずり出さない() {
    // ユーザーの指摘：3 点のメガジュカインを倒せるのに、アカギで 1 点のバタフリーを倒していた
    use pocket_engine::players::drag_is_worth;

    // 今の相手を倒せる（3 点）。1 点の引きずり出しは不要
    assert!(!drag_is_worth(1.0, 3.0, true, 1.0));
    // 今の相手（3 点）は倒せないが半分以上削れる。非 ex を引きずり出すより主力を殴る
    assert!(!drag_is_worth(1.0, 3.0, false, 0.875));
    // 今の相手をほとんど削れないなら、1 点でも引きずり出して取る
    assert!(drag_is_worth(1.0, 3.0, false, 0.2));
    // 引きずり出す先のほうがポイントが大きいなら使う
    assert!(drag_is_worth(3.0, 1.0, false, 0.9));
    // 引きずり出しても倒せないなら使わない
    assert!(!drag_is_worth(0.0, 1.0, false, 0.2));
}

#[test]
fn 倒せる主役の前でアカギを使わない() {
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");

    let mut wasted = Vec::new();
    for seed in 0..40u64 {
        let replay = play_one_game_with_log(
            &blaziken,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        for (index, step) in replay.steps.iter().enumerate() {
            if index == 0 || step.actor != 0 || !step.description.contains("「Cyrus」") {
                continue;
            }
            let before = &replay.steps[index - 1];
            let (Some(mine), Some(theirs)) = (before.active[0].as_ref(), before.active[1].as_ref())
            else {
                continue;
            };
            // メガバシャーモ（E2 以上）の前にいる相手が、弱点込み 140 + やけど 20 で倒せる HP なら無駄
            let can_burn_through = mine.name == "Mega Blaziken ex"
                && mine.energy.len() >= 2
                && theirs.remaining_hp <= 160
                && theirs.name.contains("Sceptile");
            if can_burn_through {
                wasted.push(format!(
                    "シード {seed}: {} {}/{}",
                    theirs.name, theirs.remaining_hp, theirs.max_hp
                ));
            }
        }
    }
    assert!(
        wasted.is_empty(),
        "倒せる相手の前でアカギを使った: {wasted:?}"
    );
}

#[test]
fn 逃げ先も次の攻撃で倒されるなら逃げない() {
    // ユーザーの指摘（バシャーモ先攻 シード 3 ターン 5）：10/70 のポワルンから 50/80 のクイタランへ
    // 逃げたが、クイタランも次のおひさまウインド 60 で倒された。ポイントを 1 ターン遅らせる
    // だけでエネルギーを失う。逃げ先がこの番に相手を倒せるなら例外
    use pocket_engine::players::retreat_is_pointless;

    assert!(retreat_is_pointless(false, false));
    assert!(!retreat_is_pointless(true, false));
    assert!(!retreat_is_pointless(false, true));
}

#[test]
fn 倒される逃げ先へは逃げずに殴る() {
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");

    let mut pointless = Vec::new();
    for seed in 0..40u64 {
        let replay = play_one_game_with_log(
            &blaziken,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        for (index, step) in replay.steps.iter().enumerate() {
            if index == 0 || step.actor != 0 || !step.description.contains("にげる") {
                continue;
            }
            let (Some(theirs), Some(after)) = (
                replay.steps[index - 1].active[1].as_ref(),
                step.active[0].as_ref(),
            ) else {
                continue;
            };
            // バタフリー（E1 以上）のおひさまウインド 60 で倒される逃げ先は無駄。
            // 逃げ先が E2 以上のメガバシャーモ（この番に倒せる可能性）なら除く
            let incoming = if theirs.name == "Butterfree" && !theirs.energy.is_empty() {
                60
            } else {
                continue;
            };
            let can_hit_back = after.name == "Mega Blaziken ex" && after.energy.len() >= 2;
            // 3 点のメガバシャーモを 1 点の繋ぎに替えるのは、渡すポイントが減るので無駄ではない
            let Some(before_active) = replay.steps[index - 1].active[0].as_ref() else {
                continue;
            };
            let saves_points = before_active.name.ends_with(" ex") && !after.name.ends_with(" ex");
            if after.remaining_hp <= incoming && !can_hit_back && !saves_points {
                pointless.push(format!(
                    "シード {seed}: {} {}/{} へ逃げた",
                    after.name, after.remaining_hp, after.max_hp
                ));
            }
        }
    }
    assert!(
        pointless.is_empty(),
        "倒される逃げ先へ逃げた: {pointless:?}"
    );
}

#[test]
fn 相手の打点は次の番に一個付けた前提で見積もる() {
    // 対戦ログ（シード 8 ターン 9）：デッドリーテールでエネルギーを捨てた E1 のメガジュカインを
    // 打点 0 と見て、80 HP のクイタランへ逃げて倒された
    use deckgym::models::EnergyType::Grass;
    use pocket_engine::players::attainable_damage;

    let tail = vec![(vec![Grass, Grass], 130u32)];
    assert_eq!(attainable_damage(&tail, &[Grass]), 130);
}

#[test]
fn 相手の攻撃に耐えられるなら残り体力だけで逃げない() {
    // 対戦ログ（シード 8 ターン 7）：90/210 のメガバシャーモが、ぶつかる 30 に耐えるのに
    // 残り HP が半分を切ったという理由でポワルンへ逃げ、エネルギーを失った。
    // 高 HP・高火力の主役は、倒される前に倒し切る（動画の立ち回り）
    use pocket_engine::players::retreat_danger;

    assert!((retreat_danger(90, 30) - 0.0).abs() < f64::EPSILON);
    assert!((retreat_danger(90, 130) - 1.0).abs() < f64::EPSILON);
    assert!((retreat_danger(30, 30) - 1.0).abs() < f64::EPSILON);
}

#[test]
#[allow(clippy::too_many_lines, clippy::float_cmp)]
fn ワザの効果文からダメージの増減を読む() {
    // メタ 10 デッキのワザ効果のうち、方策が読めていなかったもの。
    // 効果文の解釈は deckgym の EFFECT_MECHANIC_MAP と同じにする
    use pocket_engine::players::{AttackContext, expected_damage};

    let ctx = AttackContext {
        base_damage: 20,
        own_bench: 3,
        opponent_bench: 3,
        opponent_active_energy: 2,
        has_tool: false,
        extra_energy: 0,
        opponent_is_ex: false,
        opponent_has_ability: false,
        both_active_energy: 4,
        has_bench_basic_of_own_type: true,
        hand_size: 0,
        revealed_count: 0,
    };

    // スイクン ex「Crystal Waltz」：基礎値を置き換え、両者のベンチ 1 匹につき 20
    assert_eq!(
        expected_damage(
            Some(
                "This attack does 20 damage for each Benched Pokémon (both yours and your opponent's)."
            ),
            &ctx
        ),
        120.0
    );
    // ツボツボ ex「Triple Slap」：基礎値を置き換え、コイン 3 枚の期待値
    assert_eq!(
        expected_damage(
            Some("Flip 3 coins. This attack does 20 damage for each heads."),
            &ctx
        ),
        30.0
    );
    // ラッタ「Ambush」：+20 の期待値 +10
    assert_eq!(
        expected_damage(
            Some("Flip a coin. If heads, this attack does 20 more damage."),
            &ctx
        ),
        30.0
    );
    // メガチルタリス ex「Mega Harmony」：自分のベンチ 1 匹につき +30（既存）
    assert_eq!(
        expected_damage(
            Some("This attack does 30 more damage for each of your Benched Pokémon."),
            &AttackContext {
                base_damage: 40,
                ..ctx.clone()
            }
        ),
        130.0
    );
    // デルフォックス：相手のバトル場のエネルギー 1 個につき +30
    assert_eq!(
        expected_damage(
            Some(
                "This attack does 30 more damage for each Energy attached to your opponent's Active Pokémon."
            ),
            &AttackContext {
                base_damage: 60,
                ..ctx.clone()
            }
        ),
        120.0
    );
    // 条件付きの加算。満たすときだけ足す
    let with_tool = AttackContext {
        has_tool: true,
        ..ctx.clone()
    };
    let tool_text =
        Some("If this Pokémon has a Pokémon Tool attached, this attack does 30 more damage.");
    assert_eq!(expected_damage(tool_text, &ctx), 20.0);
    assert_eq!(expected_damage(tool_text, &with_tool), 50.0);

    let extra = AttackContext {
        base_damage: 90,
        extra_energy: 1,
        ..ctx.clone()
    };
    let pulse = Some(
        "If this Pokémon has at least 1 extra [F] Energy attached, this attack does 50 more damage.",
    );
    assert_eq!(
        expected_damage(
            pulse,
            &AttackContext {
                base_damage: 90,
                ..ctx.clone()
            }
        ),
        90.0
    );
    assert_eq!(expected_damage(pulse, &extra), 140.0);

    let ex = AttackContext {
        base_damage: 10,
        opponent_is_ex: true,
        ..ctx.clone()
    };
    assert_eq!(
        expected_damage(
            Some(
                "If your opponent's Active Pokémon is a Pokémon ex, this attack does 30 more damage."
            ),
            &ex
        ),
        40.0
    );
    let ability = AttackContext {
        base_damage: 10,
        opponent_has_ability: true,
        ..ctx.clone()
    };
    assert_eq!(
        expected_damage(
            Some(
                "If your opponent's Active Pokémon has an Ability, this attack does 40 more damage."
            ),
            &ability
        ),
        50.0
    );
    let five = AttackContext {
        base_damage: 60,
        both_active_energy: 5,
        ..ctx.clone()
    };
    let leaves = Some(
        "If the amount of Energy attached to both Active Pokémon is 5 or more, this attack does 60 more damage.",
    );
    assert_eq!(
        expected_damage(
            leaves,
            &AttackContext {
                base_damage: 60,
                ..ctx.clone()
            }
        ),
        60.0
    );
    assert_eq!(expected_damage(leaves, &five), 120.0);
    // ビークイン ex「Chase Order」：ベンチの草たねを捨てれば +70
    let order = Some(
        "You may discard 1 of your Benched Basic [G] Pokémon. If you do, this attack does 70 more damage.",
    );
    assert_eq!(
        expected_damage(
            order,
            &AttackContext {
                base_damage: 70,
                ..ctx.clone()
            }
        ),
        140.0
    );
    assert_eq!(
        expected_damage(
            order,
            &AttackContext {
                base_damage: 70,
                has_bench_basic_of_own_type: false,
                ..ctx.clone()
            }
        ),
        70.0
    );
}

#[test]
fn ベンチへのダメージと自傷とエネルギーの消費を読む() {
    use pocket_engine::players::{BenchTarget, attack_side_effects};

    // クイタラン「Tongue Whip」：相手のベンチ 1 匹に 30
    let whip = attack_side_effects(Some(
        "This attack does 30 damage to 1 of your opponent's Benched Pokémon.",
    ));
    assert_eq!(whip.bench_damage, Some((30, BenchTarget::OneBenched)));
    // フーパ ex「Shadow Bullet」：バトル場に加えてベンチ 1 匹に 20
    let bullet = attack_side_effects(Some(
        "This attack also does 20 damage to 1 of your opponent's Benched Pokémon.",
    ));
    assert_eq!(bullet.bench_damage, Some((20, BenchTarget::OneBenched)));
    // キュウコン「Blizzard」：相手のベンチ全員に 20
    let blizzard = attack_side_effects(Some(
        "This attack also does 20 damage to each of your opponent's Benched Pokémon.",
    ));
    assert_eq!(blizzard.bench_damage, Some((20, BenchTarget::EachBenched)));
    // パオジアン ex「Diving Icicles」：相手の任意の 1 匹に 130。エネルギーはすべて捨てる
    let icicles = attack_side_effects(Some(
        "Discard all [W] Energy from this Pokémon. This attack does 130 damage to 1 of your opponent's Pokémon.",
    ));
    assert_eq!(icicles.bench_damage, Some((130, BenchTarget::AnyPokemon)));
    assert!(icicles.discards_all_energy);
    // サザンドラ「Hyper Ray」：エネルギーをすべて捨てる。メガバーニング：1 個捨てる
    assert!(attack_side_effects(Some("Discard all Energy from this Pokémon.")).discards_all_energy);
    assert_eq!(
        attack_side_effects(Some("Discard Fire[R] Energy from this Pokémon. Your opponent's Active Pokémon is now Burned.")).discarded_energy,
        1
    );
    // フーパ ex「Dynamite Punch」：自分に 20
    assert_eq!(
        attack_side_effects(Some("This Pokémon also does 20 damage to itself.")).self_damage,
        20
    );
    assert_eq!(attack_side_effects(None).self_damage, 0);
}

#[test]
fn ベンチへのダメージで倒せるならその分の価値がある() {
    // ベンチ狙撃は、倒せる相手がいればきぜつと同じ価値。倒せなければ削った分だけ
    use pocket_engine::players::score_bench_damage;

    // 30 ダメージで残り 30 の 1 点を倒せる
    assert!(score_bench_damage(30, &[(30, 1.0), (60, 1.0)]) >= 1000.0);
    // 倒せなければ、最も価値のある削りを取る（0 より大きい）
    let chip = score_bench_damage(30, &[(60, 1.0), (200, 3.0)]);
    assert!(chip > 0.0 && chip < 1000.0);
    // 相手のベンチが空なら 0
    assert!((score_bench_damage(30, &[]) - 0.0).abs() < f64::EPSILON);
}

#[test]
fn バトル場にエネルギーを付けられない番は今の打点で比べる() {
    // アローラキュウコン ex「Binding Snow」の返し。「1 個付ければ撃てる打点」で
    // バトル場を評価すると、付けられないのに強いと誤認して入れ替えない
    use pocket_engine::players::active_attainable_damage;

    // 通常：1 個付ければ 120（メガバーニング）
    assert_eq!(active_attainable_damage(false, 0, 120), 120);
    // 付けられない番：今撃てる 0 のまま
    assert_eq!(active_attainable_damage(true, 0, 120), 0);
}

#[test]
fn 次の番に倒されて攻撃もできないバトル場にはエネルギーを付けない() {
    // Gemini の解析（ビークイン対ラッタ シード 11 ターン 5）：エネルギーを奪われて 30/140 に
    // なったビークイン ex（相手の打点 70）に付け直したが、2 個必要で撃てず、次の番に倒された。
    // 見捨ててベンチの主役を育てるべき
    use pocket_engine::players::attach_to_active_is_wasted;

    // 倒される・付けても撃てない・逃げる助けにもならない → 無駄
    assert!(attach_to_active_is_wasted(true, false, false));
    // 付ければ撃てるなら付ける（最後の一撃）
    assert!(!attach_to_active_is_wasted(true, true, false));
    // 付ければ逃げられるなら付ける
    assert!(!attach_to_active_is_wasted(true, false, true));
    // 倒されないなら通常どおり
    assert!(!attach_to_active_is_wasted(false, false, false));
}

#[test]
fn ベンチを犠牲にする追加ダメージは倒しきれるときだけ使う() {
    // Gemini の解析（同 ターン 11）：最後のツボツボ ex を捨てて 140 を出したが、
    // ゆうがなマントで 180 のキュウコン ex を 30 残して倒せず、次の番に負けた
    use pocket_engine::players::sacrifice_for_damage_is_worth;

    // 70 では倒せず 140 なら倒せる → 犠牲にする
    assert!(sacrifice_for_damage_is_worth(70, 140, 120));
    // 140 でも倒せない → 犠牲にしない
    assert!(!sacrifice_for_damage_is_worth(70, 140, 180));
    // 70 で既に倒せる → 犠牲にしない
    assert!(!sacrifice_for_damage_is_worth(70, 140, 60));
}

#[test]
fn 壁になるたねは前に出し一進化系はベンチで進化させる() {
    // ユーザーの指摘：ビークイン ex + ツボツボ ex はツボツボ（HP 120、受けるダメージ −20）で
    // 受けつつ削り、裏でビークインを完成させるのがセオリー。HP 50 のミツハニーを前に出していた
    use pocket_engine::players::is_wall_basic;

    // 進化系統に属さない HP 100 以上のたね、または「受けるダメージ −N」の特性持ち
    assert!(is_wall_basic(
        0,
        120,
        Some("This Pokémon takes -20 damage from attacks."),
        false
    ));
    assert!(is_wall_basic(0, 130, None, false));
    assert!(
        !is_wall_basic(0, 60, None, false),
        "HP の低いたねは壁にならない"
    );
    assert!(
        !is_wall_basic(0, 120, None, true),
        "進化系統のたねは裏で育てる"
    );
    assert!(!is_wall_basic(1, 120, None, false), "進化後は対象外");
}

#[test]
fn ツボツボがあればミツハニーではなくツボツボを前に出す() {
    use pocket_engine::replay::play_one_game_with_log;

    let shuckle = parse_deck(SHUCKLE_DECK).expect("ツボツボデッキは有効なはず");
    let opponent = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");

    let mut both = 0;
    let mut wall_front = 0;
    for seed in 0..40u64 {
        let replay = play_one_game_with_log(
            &shuckle,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        let hand = &replay.opening_hands[0];
        let has_shuckle = hand.iter().any(|c| c.name == "Shuckle ex");
        let has_combee = hand.iter().any(|c| c.name == "Combee");
        if !(has_shuckle && has_combee) {
            continue;
        }
        both += 1;
        let front = replay
            .steps
            .iter()
            .find(|s| s.turn == 0 && s.actor == 0 && s.description.contains("をバトル場に出す"))
            .map(|s| s.description.clone())
            .unwrap_or_default();
        if front.contains("Shuckle ex") {
            wall_front += 1;
        }
    }
    assert!(both > 0, "両方を初手に持つ試合がない");
    assert_eq!(
        wall_front, both,
        "ツボツボがあるのに前に出さなかった: {wall_front}/{both}"
    );
}

#[test]
fn ベンチを犠牲にするなら傷ついているほうを選ぶ() {
    use pocket_engine::players::score_sacrifice_target;

    // 同じ 1 匹を捨てるなら、残り HP の少ないほう
    assert!(score_sacrifice_target(40, 120, false) > score_sacrifice_target(120, 120, false));
    // 主役の系統は捨てない
    assert!(score_sacrifice_target(40, 120, false) > score_sacrifice_target(40, 120, true));
}

#[test]
fn 自分のスタジアムは先に出して使いドローはその後にする() {
    // ユーザーの指摘：あまくかおる森（毎ターン草のたねをサーチできるスタジアム）を
    // 博士の研究の後に出していた。先に出して使ってから引くべき
    use pocket_engine::players::{score_stadium_play, score_trainer};

    let draw = score_trainer("Professor's Research", 4, 60, 130, false);
    let forest = score_stadium_play(
        Some(
            "Once during each player's turn, that player may put a random Basic [G] Pokémon from their deck into their hand.",
        ),
        false,
    );
    assert!(
        forest > draw,
        "スタジアムがドローより後になる: {forest} <= {draw}"
    );
    // 同じスタジアムが既に自分のものとして出ているなら出し直しは無駄
    assert!(score_stadium_play(Some("Once during each player's turn, ..."), true) < 0.0);
}

#[test]
fn フィールドブロアーは相手のどうぐかスタジアムがあるときだけ使う() {
    // ユーザーの指摘：相手に剥がすものがないのに使い、自分のスタジアムをトラッシュしていた
    use pocket_engine::players::{score_discard_stadium, score_field_blower};

    assert!(
        score_field_blower(false, false) < 0.0,
        "剥がすものがないのに使う"
    );
    assert!(
        score_field_blower(true, false) > 0.0,
        "相手のどうぐがあるのに使わない"
    );
    assert!(
        score_field_blower(false, true) > 0.0,
        "相手のスタジアムがあるのに使わない"
    );
    // トラッシュする対象：自分のスタジアムなら選ばない（Noop 未満）
    assert!(score_discard_stadium(true) < 0.0);
    assert!(score_discard_stadium(false) > 0.0);
}

#[test]
fn 主役は完成するまで壁の後ろで育てる() {
    // ユーザーの指摘（ビークイン対ラッタ シード 19 ターン 3）：前で進化した E1 のビークイン ex が、
    // スピーダー 2 枚とベンチのツボツボ ex があるのに前に居座り、エネルギーを付けていた。
    // 主役はまだ撃てないなら壁と入れ替え、裏で完成させる
    use pocket_engine::players::main_should_hide_behind_wall;

    // 主役が撃てない・壁が健在 → 入れ替える
    assert!(main_should_hide_behind_wall(true, false, true));
    // 主役が撃てるなら前で殴る
    assert!(!main_should_hide_behind_wall(true, true, true));
    // 健在な壁がいなければ入れ替えない
    assert!(!main_should_hide_behind_wall(true, false, false));
    // 主役でなければ対象外
    assert!(!main_should_hide_behind_wall(false, false, true));
}

#[test]
fn 撃てないビークインは壁のツボツボと入れ替える() {
    // ユーザーの指摘（対ラッタ・キュウコン シード 19 ターン 3）：E0 のビークイン ex が
    // スピーダー 2 枚とツボツボ ex を持ちながら前に居座り、エネルギーを付けて奪われた
    use pocket_engine::replay::play_one_game_with_log;

    let shuckle = parse_deck(SHUCKLE_DECK).expect("ツボツボデッキは有効なはず");
    let opponent = parse_deck(RATICATE_DECK).expect("ラッタデッキは有効なはず");

    let mut stayed = 0;
    let mut chances = 0;
    for seed in 0..40u64 {
        let replay = play_one_game_with_log(
            &shuckle,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        for (index, step) in replay.steps.iter().enumerate() {
            // 自分の番の終わり。バトル場が E1 以下のビークイン ex、ベンチに無傷のツボツボ ex、
            // 手札にスピーダー 2 枚（= 逃げるコスト 0 にできた）なら、入れ替えるべきだった
            if step.actor != 0 || step.description != "ターンを終える" || step.turn == 0 {
                continue;
            }
            let start = replay.steps[..index]
                .iter()
                .rposition(|s| s.actor == 0 && s.description.contains("枚引く"));
            let Some(start) = start else { continue };
            let hand = &replay.steps[start].hand[0];
            let speeds = hand.iter().filter(|c| c.name == "X Speed").count();
            let Some(active) = step.active[0].as_ref() else {
                continue;
            };
            let wall_ready = step.bench[0]
                .iter()
                .any(|p| p.name == "Shuckle ex" && p.remaining_hp * 2 >= p.max_hp);
            if speeds >= 2 && active.name == "Vespiquen ex" && active.energy.len() < 2 && wall_ready
            {
                chances += 1;
                stayed += 1;
            }
        }
    }
    assert_eq!(
        stayed, 0,
        "撃てないビークイン ex が壁の後ろに下がらなかった: {stayed}/{chances}"
    );
}

#[test]
fn 受けるダメージを減らす特性を相手の打点の見積もりに入れる() {
    // ツボツボ ex「Solid Shell」（受けるダメージ −20）。メガバーニング 120 は 100 になり、
    // 120 HP のツボツボは耐える。これを見ずに「倒される」と判断して壁に下がらなかった
    use pocket_engine::players::damage_reduction_of_text;

    assert_eq!(
        damage_reduction_of_text(Some("This Pokémon takes -20 damage from attacks.")),
        20
    );
    assert_eq!(
        damage_reduction_of_text(Some("Once during your turn, you may draw a card.")),
        0
    );
    assert_eq!(damage_reduction_of_text(None), 0);
}

#[test]
fn 壁が前にいるときのエネルギーは裏の主役に付ける() {
    // 壁は受けるためにいる。エネルギーは裏で完成させる主役（ベンチのビークイン ex）へ
    use pocket_engine::players::score_attach_target_behind_wall;

    // バトル場が壁（あと 1 個で撃てる）と、ベンチの主役（あと 2 個）
    let wall_active = score_attach_target_behind_wall(true, 1, true, false);
    let main_bench = score_attach_target_behind_wall(false, 2, true, true);
    assert!(
        main_bench > wall_active,
        "壁にエネルギーを付けた: 壁 {wall_active} >= 主役 {main_bench}"
    );
    // 壁が前にいなければ従来どおり（前のあと 1 個が優先）
    let normal_active = score_attach_target_behind_wall(true, 1, false, false);
    let normal_bench = score_attach_target_behind_wall(false, 2, false, true);
    assert!(normal_active > normal_bench);
}

#[test]
fn 壁の後ろに下がる主役は進化する前に逃げる() {
    // ユーザーの指摘：ミツハニー（逃げるコスト 1）のうちに逃げれば済むのに、先に
    // ビークイン ex（コスト 2）へ進化してから逃げようとして、逃げられなくなっていた
    use pocket_engine::players::evolve_before_hiding_is_premature;

    // 下がる予定で、今の姿なら逃げられる → 進化は逃げた後
    assert!(evolve_before_hiding_is_premature(true, true));
    // 逃げられないなら進化して耐える
    assert!(!evolve_before_hiding_is_premature(true, false));
    // 下がる予定がなければ通常どおり
    assert!(!evolve_before_hiding_is_premature(false, true));
}

#[test]
fn ベンチでは進化前より進化済みの主役にエネルギーを付ける() {
    // ユーザーの指摘（対ラッタ シード 10 ターン 3）：進化したばかりのビークイン ex（あと 2 個で 140）
    // ではなく、予備のミツハニー（あと 1 個で 30）に付けていた
    use pocket_engine::players::{bench_attach_tiebreak, score_attach_target};

    let combee = score_attach_target(false, 0, 1, 1, true) + bench_attach_tiebreak(30, 0);
    let vespiquen = score_attach_target(false, 0, 2, 2, true) + bench_attach_tiebreak(70, 1);
    assert!(
        vespiquen > combee,
        "進化前のミツハニーに付けた: ミツハニー {combee} >= ビークイン {vespiquen}"
    );
}

#[test]
fn 初期配置ではポイントの重いポケモンを前に出さない() {
    // ユーザーの指摘（サザンドラ対バタフリー seed1）：初手にオトシドリがあるのに
    // メガアブソル ex（倒されたら 3 点）を前に出し、進化前に殴られて負け筋になっていた。
    // 壁の加点は 1 点のポケモンに限り、3 点のポケモンは大きく減点する
    use pocket_engine::players::setup_points_penalty;

    assert!((setup_points_penalty(1.0) - 0.0).abs() < f64::EPSILON);
    assert!(setup_points_penalty(2.0) > 0.0);
    assert!(setup_points_penalty(3.0) > setup_points_penalty(2.0));
    // 壁の加点（60 + 20）を打ち消して余る大きさ
    assert!(setup_points_penalty(3.0) > 80.0);
}

#[test]
fn オトシドリがあればメガアブソルではなくオトシドリを前に出す() {
    use pocket_engine::replay::play_one_game_with_log;

    let hydreigon = parse_deck(HYDREIGON_DECK).expect("サザンドラデッキは有効なはず");
    let opponent = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");

    let mut both = 0;
    let mut mega_front = 0;
    for seed in 0..60u64 {
        let replay = play_one_game_with_log(
            &hydreigon,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        let hand = &replay.opening_hands[0];
        let has_mega = hand.iter().any(|c| c.name == "Mega Absol ex");
        // デイノしかない場合はデイノをベンチで育てる必要があり、メガアブソルが前でもよい
        // （ユーザーの指摘：進化ラインやふしぎなアメが手元にあるならまだしも）
        let has_other = hand.iter().any(|c| c.name == "Bombirdier");
        if !(has_mega && has_other) {
            continue;
        }
        both += 1;
        let front = replay
            .steps
            .iter()
            .find(|s| s.turn == 0 && s.actor == 0 && s.description.contains("をバトル場に出す"))
            .map(|s| s.description.clone())
            .unwrap_or_default();
        if front.contains("Mega Absol ex") {
            mega_front += 1;
        }
    }
    assert!(both > 0, "メガアブソルと他のたねを初手に持つ試合がない");
    assert_eq!(
        mega_front, 0,
        "メガアブソル ex を前に出した: {mega_front}/{both}"
    );
}

#[test]
fn ポイントの重いバトル場を軽い逃げ先に替えるのは無駄ではない() {
    // ユーザーの指摘（サザンドラ対バタフリー seed1 ターン 7）：20/170 のメガアブソル ex（3 点）を
    // 前に残し、E3 のサザンドラ（1 点、ハイパーレイ 130 が撃てる）をベンチに置いたまま番を終えた。
    // 逃げ先が倒されても、渡すポイントが減る・先に殴れるなら逃げる価値がある
    use pocket_engine::players::retreat_destination_is_useful;

    // 3 点 → 1 点に替わる（主役がこの番に撃てないとき）
    assert!(retreat_destination_is_useful(false, false, 3.0, 1.0));
    // 主役がこの番に撃てても、3 点 → 1 点なら逃げる。
    // 当初はここを「殴ってから倒されるほうが得」として false にしていたが、
    // ユーザーの指摘（seed27 ターン 11）で 2 点の差のほうが大きいと分かった。
    // 詳しくは `三点のメガを一点の繋ぎに替えられるなら撃てても逃げる`
    assert!(retreat_destination_is_useful(false, true, 3.0, 1.0));
    // 1 点しか減らないなら、殴ってから倒されるほうが得のまま
    assert!(!retreat_destination_is_useful(false, true, 3.0, 2.0));
    // 倒せる
    assert!(retreat_destination_is_useful(true, false, 1.0, 1.0));
    // どちらもなし → 無駄（先に 20 殴れる程度では繋ぎとエネルギーを失うだけ）
    assert!(!retreat_destination_is_useful(false, false, 1.0, 1.0));
}

#[test]
fn エネルギーが足りているなら自傷する加速の特性を使わない() {
    // 同 seed1 ターン 7：E3 でハイパーレイが撃てるサザンドラに「Roar in Unison」を重ね、
    // 30 の自傷だけ受けていた
    use pocket_engine::players::score_self_damaging_ability;

    let needed = score_self_damaging_ability(150, 30, true, true);
    let surplus = score_self_damaging_ability(150, 30, true, false);
    assert!(needed > 0.0, "エネルギーが要るのに使わない");
    assert!(surplus < 0.0, "足りているのに自傷して使った: {surplus}");
    // 自傷しない特性は従来どおり
    assert!(score_self_damaging_ability(150, 0, false, false) > 0.0);
}

#[test]
fn 撃てない三点のメガは一点のポケモンの後ろに置く() {
    // ユーザーの指摘（バシャーモ対バタフリー seed8 ターン 3）：バトル場のアチャモを
    // ふしぎなアメでメガバシャーモ ex にし、E1 で撃てないまま前に放置して殴られ続けた。
    // 壁がいなくても、3 点のメガ ex は 1 点のポケモンの後ろで育てる
    use pocket_engine::players::is_shelter_for;

    // 3 点を守る：健在な 1 点のポケモンなら壁でなくてもよい
    assert!(is_shelter_for(3.0, 1.0, true, false));
    // 瀕死の 1 点は盾にならない
    assert!(!is_shelter_for(3.0, 1.0, false, false));
    // 2 点（ex）の後ろには隠さない
    assert!(!is_shelter_for(3.0, 2.0, true, false));
    // 1 点の主役は壁がいるときだけ
    assert!(is_shelter_for(1.0, 1.0, true, true));
    assert!(!is_shelter_for(1.0, 1.0, true, false));
}

#[test]
fn この番に倒せるなら逃げてワザを捨てない() {
    // ユーザーの指摘（`docs/play-principles.md`）：「撃てるなら殴ってから倒されるほうが得」。
    // シード 16 ターン 9 で、E2 のメガバシャーモ ex（メガバーニング 120、相手は炎弱点で 140）が
    // 残り 80 のメガジュカイン ex を倒せば 3 点で勝ちだったのに、逃げて撃たずに終えていた。
    // 逃げ先も倒せるなら替えてよい（強いほうで殴る）
    use pocket_engine::players::retreat_wastes_knockout;

    assert!(
        retreat_wastes_knockout(true, false),
        "バトル場が倒せて逃げ先が倒せないなら、逃げるのはワザを捨てること"
    );
    assert!(
        !retreat_wastes_knockout(true, true),
        "逃げ先も倒せるなら、替えてから殴ってよい"
    );
    assert!(
        !retreat_wastes_knockout(false, false),
        "倒せないなら逃げの判断は他の基準に任せる"
    );
    assert!(
        !retreat_wastes_knockout(false, true),
        "逃げ先だけが倒せるなら、逃げるのが正しい"
    );
}

#[test]
fn 勝ちを決められる番に逃げない() {
    // 同じ局面を対戦で確かめる。シード 16 のターン 9 に自分がワザを撃つこと
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");
    let replay =
        play_one_game_with_log(&blaziken, &opponent, Strategy::Pocket, Strategy::Pocket, 16)
            .expect("対戦できるはず");

    let moves: Vec<&str> = replay
        .steps
        .iter()
        .filter(|s| s.actor == 0 && s.turn == 9)
        .map(|s| s.description.as_str())
        .collect();
    assert!(
        moves.iter().any(|m| m.contains("がワザ「")),
        "残り 80 の相手を 140 で倒せる番に撃っていない: {moves:?}"
    );
}

#[test]
fn 三点のメガを一点の繋ぎに替えられるなら撃てても逃げる() {
    // ユーザーの指摘（バシャーモ対バタフリー seed27 ターン 11）：バトル場のメガバシャーモ ex が
    // 10/210 E2。撃てるが相手（210/210）は倒せず、次の番に倒されて 3 点を渡す。
    // 「ほぼ積んでいるが、勝ち筋としては Heatmor に逃げて無傷のメガバシャーモにエネ貼り」。
    // 「撃てるなら殴ってから倒されるほうが得」は渡すポイントが変わらないときの話で、
    // 3 点を 1 点に替えられるなら殴れても逃げる
    use pocket_engine::players::retreat_destination_is_useful;

    assert!(
        retreat_destination_is_useful(false, true, 3.0, 1.0),
        "撃てても、3 点のメガを 1 点の繋ぎに替えられるなら逃げる"
    );
    assert!(
        !retreat_destination_is_useful(false, true, 3.0, 2.0),
        "1 点しか減らないなら、殴ってから倒されるほうが得"
    );
    assert!(
        !retreat_destination_is_useful(false, true, 1.0, 1.0),
        "渡すポイントが減らないなら逃げる意味がない"
    );
    assert!(
        retreat_destination_is_useful(false, false, 3.0, 2.0),
        "撃てないなら、1 点減るだけでも逃げる（既存の定石）"
    );
    assert!(
        retreat_destination_is_useful(true, true, 1.0, 3.0),
        "逃げ先がこの番に倒せるなら、渡すポイントが増えても替える（既存の定石）"
    );
}

#[test]
fn 前に出たら倒される主役より健在な主役を先に育てる() {
    // ユーザーの指摘（seed27 ターン 11）：「Heatmor に逃げて無傷のメガバシャーモにエネ貼り」。
    // 10/210 のメガに付けても、前に出た番に倒されて 3 点を渡すだけ
    use pocket_engine::players::dying_bench_main_is_deprioritised;

    assert!(
        dying_bench_main_is_deprioritised(true, true),
        "倒される主役より、健在な主役を先に育てる"
    );
    assert!(
        !dying_bench_main_is_deprioritised(true, false),
        "健在な代わりがいないなら、倒される主役でも育てるしかない"
    );
    assert!(
        !dying_bench_main_is_deprioritised(false, true),
        "耐えられるなら下げない"
    );
}

#[test]
fn 瀕死のメガではなく無傷のメガにエネルギーを付ける() {
    // 同じ局面を対戦で確かめる。シード 27 のターン 11 の終わりに、
    // 無傷のメガバシャーモ ex にエネルギーが付いていること
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");
    let replay =
        play_one_game_with_log(&blaziken, &opponent, Strategy::Pocket, Strategy::Pocket, 27)
            .expect("対戦できるはず");

    let end = replay
        .steps
        .iter()
        .find(|s| s.actor == 0 && s.turn == 11 && s.description == "ターンを終える")
        .expect("ターン 11 を終える手があるはず");
    let healthy: Vec<_> = end.bench[0]
        .iter()
        .filter(|p| p.name == "Mega Blaziken ex" && p.remaining_hp == p.max_hp)
        .collect();
    assert!(
        !healthy.is_empty(),
        "無傷のメガバシャーモ ex がベンチにいるはず"
    );
    let bench: Vec<String> = end.bench[0]
        .iter()
        .map(|p| {
            format!(
                "{} {}/{} E{}",
                p.name,
                p.remaining_hp,
                p.max_hp,
                p.energy.len()
            )
        })
        .collect();
    assert!(
        healthy.iter().any(|p| !p.energy.is_empty()),
        "無傷のメガにエネルギーが付いていない: {bench:?}"
    );
}

#[test]
fn 相手の逃げエネを増やす札は打点に乗る番だけ使う() {
    // ユーザーの指摘：「アリアドスの特性 + ロケット団のベトベトバズーカで逃げエネを増やして
    // 一気に殴る」がセオリー。効果は相手の次の番の終わりまでしか続かないので、
    // くさむすびを撃つ番に使わないと乗らないまま切れる。
    // 実測（メタ 10 件 × 40 試合）では、使った 583 回のうち 81% が撃たずに終わっていた
    use pocket_engine::players::retreat_cost_boost_is_worth;

    assert!(
        retreat_cost_boost_is_worth(true, true),
        "逃げエネで打点が上がるワザをこの番に撃てるなら使う"
    );
    assert!(
        !retreat_cost_boost_is_worth(true, false),
        "撃てない番に使っても、乗らないまま切れる"
    );
    assert!(
        !retreat_cost_boost_is_worth(false, true),
        "撃てても、逃げエネで打点が上がらないワザなら意味がない"
    );
    assert!(!retreat_cost_boost_is_worth(false, false), "どちらもなし");
}

#[test]
fn ベトベトバズーカをくさむすびの番に使う() {
    // ユーザーの指摘：「アリアドスの特性 + バズーカで逃げエネを増やして一気に殴る」がセオリー。
    // 効果は相手の次の番の終わりまでしか続かないので、撃たない番に使うと乗らないまま切れる。
    // 実測（メタ 10 件 × 40 試合）では、修正前は 583 回中 471 回（81%）が無駄だった
    use pocket_engine::replay::play_one_game_with_log;

    let elf = parse_deck(ELF_BAZOOKA_DECK).expect("エルフバズーカデッキは有効なはず");
    let opponent = parse_deck(LUCARIO_DECK).expect("メガルカリオデッキは有効なはず");

    let mut used = 0usize;
    let mut wasted = 0usize;
    for seed in 0..40u64 {
        let replay =
            play_one_game_with_log(&elf, &opponent, Strategy::Pocket, Strategy::Pocket, seed)
                .expect("対戦できるはず");
        let mut turns: std::collections::BTreeMap<u8, (bool, bool)> =
            std::collections::BTreeMap::new();
        for step in &replay.steps {
            if step.actor != 0 {
                continue;
            }
            let entry = turns.entry(step.turn).or_insert((false, false));
            if step.description.contains("Goo-zooka") {
                entry.0 = true;
            }
            if step.description.contains("Grass Knot") {
                entry.1 = true;
            }
        }
        for (bazooka, knot) in turns.values() {
            if *bazooka {
                used += 1;
                if !*knot {
                    wasted += 1;
                }
            }
        }
    }
    assert!(used > 0, "バズーカを一度も使っていない");
    let ratio = wasted as f64 / used as f64;
    assert!(
        ratio <= 0.40,
        "撃たない番の使用が多すぎる: {wasted}/{used}（{:.0}%）",
        ratio * 100.0
    );
}

#[test]
fn 撃てないメガバシャーモを前に放置しない() {
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");

    let mut exposed = Vec::new();
    for seed in 0..40u64 {
        let replay = play_one_game_with_log(
            &blaziken,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        for (index, step) in replay.steps.iter().enumerate() {
            // 自分の番の終わり（ターン 3 以降）に、撃てない（E1 以下の）メガバシャーモ ex を
            // この番に「バトル場で進化させた」か「逃げ先として前に出した」のに、ベンチに健在な
            // 1 点のポケモンがいるのは、撃てないメガを前に置きに行った誤り。
            // 前にいた E1 のメガに付けて次の番に殴るのは定石どおりなので対象外
            if step.actor != 0 || step.description != "ターンを終える" || step.turn < 3 {
                continue;
            }
            let Some(active) = step.active[0].as_ref() else {
                continue;
            };
            if active.name != "Mega Blaziken ex" || active.energy.len() >= 2 {
                continue;
            }
            let this_turn: Vec<_> = replay.steps[..index]
                .iter()
                .rev()
                .take_while(|s| s.turn == step.turn)
                .filter(|s| s.actor == 0)
                .collect();
            let evolved_in_front = this_turn
                .iter()
                .any(|s| s.description == "バトル場のポケモンを Mega Blaziken ex に進化");
            let retreated_in = this_turn.iter().any(|s| s.description.contains("にげる"));
            // メガバーニングでエネルギーを捨てた直後の E1 は撃った結果なので対象外
            let attacked = this_turn.iter().any(|s| s.description.contains("がワザ「"));
            if !(evolved_in_front || retreated_in) || attacked {
                continue;
            }
            let shelter = step.bench[0]
                .iter()
                .any(|p| !p.name.ends_with(" ex") && p.remaining_hp * 2 >= p.max_hp);
            if shelter {
                let moves: Vec<&str> = this_turn
                    .iter()
                    .rev()
                    .map(|s| s.description.as_str())
                    .collect();
                let bench: Vec<String> = step.bench[0]
                    .iter()
                    .map(|p| format!("{} {}/{}", p.name, p.remaining_hp, p.max_hp))
                    .collect();
                exposed.push(format!(
                    "シード {seed} T{}: E{} 手順={moves:?} ベンチ={bench:?}",
                    step.turn,
                    active.energy.len()
                ));
            }
        }
    }
    // 40 シード中 1 件だけ残る（シード 27 T11）。バトル場のメガが 10/210 で、
    // 撃っても相手を倒せず、撃てば次の番に倒されて 3 点を渡す局面。
    // 1 点の繋ぎ（Heatmor）へ逃げるか無傷のメガへ逃げるかは判断が割れるので、
    // ユーザーの確認待ち（`docs/play-principles.md` の既知の抜け）。
    // シード 16 の「倒せる番に逃げた」ぶんは `この番に倒せるなら逃げてワザを捨てない` で直した
    assert!(
        exposed.len() <= 1,
        "撃てないメガバシャーモを前に置きに行った: {exposed:?}"
    );
}

#[test]
fn ベンチで進化した主役がいるなら倒される進化前のバトル場ではなくそちらに付ける() {
    // ユーザーの指摘（バシャーモ対バタフリー seed5 ターン 3）：ベンチのアチャモをメガバシャーモ ex に
    // したのに、次の番に倒される 30/60 のバトル場のアチャモにエネルギーを付け、逃げるコストに
    // 捨てていた。バトル場の「あと 1 個」を最優先するのは進化済みの主役だけ
    use pocket_engine::players::{bench_attach_tiebreak, score_attach_target_with_stage};

    let torchic_front = score_attach_target_with_stage(true, 0, 1, 1, true, false) + 0.0;
    let mega_bench =
        score_attach_target_with_stage(false, 0, 2, 2, true, true) + bench_attach_tiebreak(120, 2);
    assert!(
        mega_bench > torchic_front,
        "進化前の前に付けた: 前 {torchic_front} >= 裏のメガ {mega_bench}"
    );
    // 前にいるのが進化済みの主役（E1 のメガ）なら、そちらが最優先（既存の定石）
    let mega_front = score_attach_target_with_stage(true, 1, 2, 2, true, true);
    let mega_bench2 =
        score_attach_target_with_stage(false, 0, 2, 2, true, true) + bench_attach_tiebreak(120, 2);
    assert!(mega_front > mega_bench2);
}

#[test]
fn ベンチで進化したばかりのメガにその番のエネルギーを付ける() {
    use pocket_engine::replay::play_one_game_with_log;

    let blaziken = parse_deck(BLAZIKEN_DECK).expect("メガバシャーモデッキは有効なはず");
    let opponent = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");

    let mut wrong = Vec::new();
    for seed in 0..40u64 {
        let replay = play_one_game_with_log(
            &blaziken,
            &opponent,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        let mut turn_evolved_bench: Option<(u8, String)> = None;
        for step in &replay.steps {
            if step.actor != 0 {
                continue;
            }
            if let Some(rest) = step
                .description
                .strip_suffix("のポケモンを Mega Blaziken ex に進化")
                && rest.starts_with("ベンチ")
            {
                turn_evolved_bench = Some((step.turn, rest.to_string()));
            }
            if step.description.starts_with("炎エネルギー1個を")
                && let Some((turn, slot)) = &turn_evolved_bench
            {
                let active_is_mega = step.active[0]
                    .as_ref()
                    .is_some_and(|p| p.name == "Mega Blaziken ex");
                // 別のメガバシャーモ（より撃てるほう）に付けるのは可
                let to_other_mega = step.bench[0].iter().enumerate().any(|(i, p)| {
                    p.name == "Mega Blaziken ex"
                        && step.description.contains(&format!("ベンチ{}", i + 1))
                });
                if *turn == step.turn
                    && !active_is_mega
                    && !step.description.contains(slot.as_str())
                    && !to_other_mega
                {
                    wrong.push(format!(
                        "シード {seed} T{}: {}",
                        step.turn, step.description
                    ));
                }
            }
            if step.description == "ターンを終える" {
                turn_evolved_bench = None;
            }
        }
    }
    assert!(
        wrong.is_empty(),
        "進化したばかりのメガに付けなかった: {wrong:?}"
    );
}

#[test]
fn 進化しないたねの主役は進化済みと同じ重みで育てる() {
    use pocket_engine::players::score_attach_target_with_stage;

    // ユーザーの依頼（サザンドラ対バタフリーの敗因分析）：メガアブソル ex はたねだが
    // それ以上進化しない最終形（HP170、悪 2 個で 80）。「進化前の主役」の重みで扱うと
    // エネルギーが分散し、94 試合中 51% でワザに必要な 2 個に届いていなかった
    let evolved = score_attach_target_with_stage(false, 1, 2, 2, true, true);
    let evolving_basic = score_attach_target_with_stage(false, 1, 2, 2, true, false);
    assert!(
        evolved > evolving_basic,
        "完成形は進化前より優先されるはず: {evolved} と {evolving_basic}"
    );
}

#[test]
fn 昇格では動けない置物より撃てる駒を選ぶ() {
    use pocket_engine::players::{Promotion, score_promotion_with_hp};

    // ユーザーの依頼で調べたメガチルタリス戦：エーフィがきぜつした後、ベンチから
    // ダークライ（3 エネ必要・にげ 2）を E0 で前に出し、撃つことも逃げることもできない
    // 番が続いていた（撃てない番の 69% がこの形）。0 エネで撃てて眠らせられる
    // ププリン（HP30・にげ 0）を前に出すべき。
    // ダークライ: 撃てない・逃げられない・耐える・HP 満タン
    let darkrai = score_promotion_with_hp(Promotion {
        ready_damage: 0,
        has_energy: false,
        points_if_ko: 1.0,
        opponent_points: 0,
        survives: true,
        remaining_hp: 100,
        max_hp: 100,
        can_retreat: false,
        ability_needs_active: false,
    });
    // ププリン: 0 エネで 10 ダメージ・にげ 0 なので逃げられる・倒される・HP 満タン
    let igglybuff = score_promotion_with_hp(Promotion {
        ready_damage: 10,
        has_energy: false,
        points_if_ko: 1.0,
        opponent_points: 0,
        survives: false,
        remaining_hp: 30,
        max_hp: 30,
        can_retreat: true,
        ability_needs_active: false,
    });
    assert!(
        igglybuff > darkrai,
        "動ける駒を優先すべき: ププリン {igglybuff} と ダークライ {darkrai}"
    );
}

#[test]
fn 手札の枚数で伸びるワザの打点を読む() {
    use pocket_engine::players::{AttackContext, expected_damage};

    // ロケット団のヤドキング ex「ハンドキネシス」は手札 1 枚につき 20。
    // 基礎値の 20 だけを見ていたため、最大 200 出るワザを 20 と評価し、
    // 手札を使い切ってから撃つ手順になっていた（ユーザーの指摘）
    let context = AttackContext {
        base_damage: 20,
        hand_size: 6,
        ..AttackContext::default()
    };
    let effect = Some("This attack does 20 damage for each card in your hand.");
    assert!(
        (expected_damage(effect, &context) - 120.0).abs() < f64::EPSILON,
        "手札 6 枚なら 120 のはず: {}",
        expected_damage(effect, &context)
    );
}

#[test]
fn 見せた枚数で伸びるワザの打点を読む() {
    use pocket_engine::players::{AttackContext, expected_damage};

    // 「こいぬまみれ」は場と手札にいる同じワザ持ちの数だけ 20 ずつ。
    // 数え方はカードによるので、呼び出し側が数えた枚数を使う
    let context = AttackContext {
        base_damage: 20,
        revealed_count: 4,
        ..AttackContext::default()
    };
    let effect = Some(
        "Reveal all of your Pokémon in play and in your hand that have the Puppy Pile attack, \
         and this attack does 20 damage for each Pokémon you revealed in this way.",
    );
    assert!(
        (expected_damage(effect, &context) - 80.0).abs() < f64::EPSILON,
        "4 匹見せたなら 80 のはず: {}",
        expected_damage(effect, &context)
    );
}

/// どうぐの効果には条件が付いているものがある。条件を満たさない付け先は無駄。
///
/// ユーザーの指摘：小さなふうせん（たね限定）を 2 進化のメガチルタリス ex に付けていた
/// （`docs/analysis/altaria-vs-blaziken/report-s14.html` の 59〜60 手目）。
/// 調べると 6 種中 4 種で条件を見ていなかった。
#[test]
fn tools_with_conditions_are_useless_on_the_wrong_target() {
    use pocket_engine::players::{TRAINER_USELESS_VALUE, score_tool_target_for};

    // 小さなふうせん：たね限定
    assert!(
        score_tool_target_for("Small Balloon", true, true, false, 0, Some("Psychic"))
            > TRAINER_USELESS_VALUE,
        "たねには効く"
    );
    assert!(
        score_tool_target_for("Small Balloon", true, true, false, 2, Some("Psychic"))
            <= TRAINER_USELESS_VALUE,
        "2 進化に付けても効果がない"
    );

    // ウォーターボート：水限定
    assert!(
        score_tool_target_for("Inflatable Boat", true, true, false, 0, Some("Water"))
            > TRAINER_USELESS_VALUE
    );
    assert!(
        score_tool_target_for("Inflatable Boat", true, true, false, 0, Some("Fire"))
            <= TRAINER_USELESS_VALUE,
        "水以外には効果がない"
    );

    // リーフマント：草限定
    assert!(
        score_tool_target_for("Leaf Cape", false, false, true, 1, Some("Grass"))
            > TRAINER_USELESS_VALUE
    );
    assert!(
        score_tool_target_for("Leaf Cape", false, false, true, 1, Some("Fire"))
            <= TRAINER_USELESS_VALUE,
        "草以外には効果がない"
    );

    // ゆうがなマント：1 進化限定
    assert!(
        score_tool_target_for("Elegant Cape", false, false, true, 1, Some("Psychic"))
            > TRAINER_USELESS_VALUE
    );
    assert!(
        score_tool_target_for("Elegant Cape", false, false, true, 0, Some("Psychic"))
            <= TRAINER_USELESS_VALUE,
        "たねには効果がない"
    );
    assert!(
        score_tool_target_for("Elegant Cape", false, false, true, 2, Some("Psychic"))
            <= TRAINER_USELESS_VALUE,
        "2 進化にも効果がない"
    );

    // 大きなマント：条件なし。どこに付けても無駄にはならない
    assert!(
        score_tool_target_for("Giant Cape", false, false, true, 2, Some("Fire"))
            > TRAINER_USELESS_VALUE
    );
}

/// 特性の効果範囲で、前に出すかベンチに置くかが決まる。
///
/// ユーザーの指摘：「このデッキにおけるダークライの立ち位置は何か？
/// 特性で眠り状態のポケモンに 20 ダメージを与え続けられることだろ。
/// だからバトル場に置くんじゃなくてベンチに出すの」
///
/// - イーブイの Boosted Evolution は「バトル場にいる間」が条件 → 前に出す価値がある
/// - ダークライの Bad Dreams は場にいれば働く → 前に出す理由がない
#[test]
fn abilities_decide_where_a_pokemon_belongs() {
    use pocket_engine::players::ability_needs_active_spot;

    assert!(
        ability_needs_active_spot(
            "As long as this Pokémon is in the Active Spot, it can evolve during your first turn \
             or the turn you play it."
        ),
        "バトル場にいることが条件の特性"
    );
    assert!(
        !ability_needs_active_spot(
            "At the end of each turn, if your opponent's Active Pokémon is Asleep, do 20 damage \
             to that Pokémon."
        ),
        "相手のバトルポケモンを見ているだけで、自分がバトル場にいる必要はない"
    );
    assert!(
        !ability_needs_active_spot("Once during your turn, you may attach a [P] Energy."),
        "場所を問わない特性"
    );
}

/// 特性の働く場所を、前に出す判断に反映する。
#[test]
fn promotion_prefers_a_pokemon_whose_ability_needs_the_active_spot() {
    use pocket_engine::players::{Promotion, score_promotion_with_hp};

    // 同じ性能で、特性の条件だけが違う 2 体
    let base = Promotion {
        ready_damage: 10,
        has_energy: false,
        points_if_ko: 1.0,
        opponent_points: 0,
        survives: true,
        remaining_hp: 50,
        max_hp: 50,
        can_retreat: true,
        ability_needs_active: false,
    };
    let needs_active = Promotion {
        ability_needs_active: true,
        ..base
    };
    assert!(
        score_promotion_with_hp(needs_active) > score_promotion_with_hp(base),
        "バトル場でしか働かない特性を持つほうを前に出す"
    );
}

/// 場にいるだけで働く特性を持つポケモンは、初期配置で前に出さない。
///
/// ユーザーの指摘：ダークライ（HP 100）は壁の条件を満たすので前に出されていたが、
/// このデッキでの役割は特性 Bad Dreams（各ターン終了時、ねむりの相手に 20）で、
/// **ベンチにいれば働く**。前に出すとイーブイの進化加速を捨てることになる。
#[test]
fn setup_keeps_a_bench_ability_pokemon_off_the_active_spot() {
    use pocket_engine::players::setup_bench_ability_penalty;

    assert!(
        setup_bench_ability_penalty(true, false) > 0.0,
        "場所を問わない特性なら、前に出す理由が減る"
    );
    assert!(
        setup_bench_ability_penalty(true, true).abs() < f64::EPSILON,
        "バトル場が条件の特性なら減点しない"
    );
    assert!(
        setup_bench_ability_penalty(false, false).abs() < f64::EPSILON,
        "特性がなければ関係ない"
    );
}

/// 特性を活かすポケモンは、初期配置でバトル場に出さない。
///
/// ユーザーの指摘：「ダークライという特性を活かすポケモンの扱い方を変えるべき」。
/// ダークライは特性 Bad Dreams（場所を問わず働く）＋ワザが無無無（3 エネ）なので、
/// 前に出しても働かず、撃つまでに 3 番かかる。ベンチで特性だけ働かせるのが役割。
///
/// ププリン（0 エネで撃てる）やイーブイ（特性がバトル場条件）は該当しない。
#[test]
fn a_bench_role_basic_is_recognised() {
    use pocket_engine::players::is_bench_role;

    // ダークライ：特性は場所を問わず働き、ワザは 3 エネ
    assert!(is_bench_role(true, false, 3), "特性目当てで、すぐ撃てない");
    // ププリン：特性なし、0 エネで撃てる
    assert!(
        !is_bench_role(false, false, 0),
        "0 エネで撃てるなら前に出す"
    );
    // イーブイ：特性がバトル場条件
    assert!(
        !is_bench_role(true, true, 1),
        "バトル場でしか働かない特性なら前に出す"
    );
    // 特性を持ち、かつ 1 エネで撃てるポケモンは前に出しても働く
    assert!(
        !is_bench_role(true, false, 1),
        "すぐ撃てるなら前に出してよい"
    );
    // 特性がなければ、コストが重くても「特性を活かす」役割ではない
    assert!(!is_bench_role(false, false, 3), "特性がなければ該当しない");
}

/// 育ったアタッカーは、たとえ「特性を活かす」役割でも前に出す。
///
/// ユーザーの懸念：「すでにベンチで育っているアタッカーより
/// 逃げエネの少ないポケモンが優先されないか」。
/// エネルギーが乗って撃てるなら、それは特性要員ではなくアタッカーとして扱う。
#[test]
fn a_grown_attacker_is_not_treated_as_a_bench_role() {
    use pocket_engine::players::is_bench_role_now;

    // ダークライ：特性あり・ワザ 3 エネ
    assert!(
        is_bench_role_now(true, false, 3, 0),
        "エネルギーが無ければ特性要員"
    );
    assert!(
        is_bench_role_now(true, false, 3, 2),
        "まだ撃てないなら特性要員のまま"
    );
    assert!(
        !is_bench_role_now(true, false, 3, 3),
        "撃てるだけ乗っていればアタッカーとして扱う"
    );
    assert!(
        !is_bench_role_now(true, false, 3, 4),
        "余分に乗っていても同じ"
    );
}

/// タイプ条件のあるトレーナーズは、そのタイプのポケモンが場にいなければ無駄。
///
/// ユーザーの指摘：探索がメガチルタリス（超）のデッキにコルニ（闘ポケモン限定の
/// ダメージバフ）を入れ、方策が 40 試合で 30 回も打っていた。
/// 該当するカードは 20 件（エリカ、カスミ、コルニなど 6 タイプ）。
#[test]
fn a_trainer_that_needs_a_type_is_useless_without_it() {
    use pocket_engine::players::required_type_of;

    assert_eq!(
        required_type_of(
            "During this turn, attacks used by your [F] Pokémon do +30 damage to your \
             opponent's Active Pokémon ex."
        ),
        Some("Fighting"),
        "コルニは闘ポケモンが要る"
    );
    assert_eq!(
        required_type_of("Heal 50 damage from 1 of your [G] Pokémon."),
        Some("Grass"),
        "エリカは草ポケモンが要る"
    );
    assert_eq!(
        required_type_of("Draw 2 cards."),
        None,
        "条件のないカードは対象外"
    );
    // 相手のポケモンを指す記述は自分の条件ではない
    assert_eq!(
        required_type_of("Put 1 random [W] Pokémon from your opponent's deck into their hand."),
        None,
        "「your」に続く型指定だけを見る"
    );
}
