//! 先読み方策 `l` のテスト。
//!
//! `p` は行動 1 つずつを静的に採点するため、手順の矛盾（付けた直後に逃げて捨てる、
//! 撃てないメガを前に置きに行く）を見抜けない。`l` は番の終わりの盤面で比べる。

use pocket_engine::deck::parse_deck;
use pocket_engine::game::{Strategy, play_one_game};
use pocket_engine::replay::play_one_game_with_log;

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
fn 方策コードlを解釈できる() {
    let strategy: Strategy = "l".parse().expect("l は有効な方策コード");
    assert_eq!(strategy, Strategy::Lookahead);
    assert_eq!(strategy.code(), "l");
}

#[test]
fn 同じシードなら同じ結果になる() {
    let a = parse_deck(BLAZIKEN_DECK).unwrap();
    let b = parse_deck(BUTTERFREE_DECK).unwrap();
    for seed in 0..5u64 {
        let x = play_one_game(&a, &b, Strategy::Lookahead, Strategy::Lookahead, seed).unwrap();
        let y = play_one_game(&a, &b, Strategy::Lookahead, Strategy::Lookahead, seed).unwrap();
        assert_eq!(x, y, "シード {seed} で結果が揺れた");
    }
}

#[test]
fn 付けた直後に逃げてエネルギーを捨てない() {
    // ユーザーの指摘（バシャーモ対バタフリー seed5 ターン 3）：次の番に倒されるアチャモに付けて、
    // そのエネルギーで逃げていた。番の終わりの盤面で比べれば、付けて捨てる手順は選ばれない
    let a = parse_deck(BLAZIKEN_DECK).unwrap();
    let b = parse_deck(BUTTERFREE_DECK).unwrap();
    let mut wasted = Vec::new();
    for seed in 0..30u64 {
        let replay =
            play_one_game_with_log(&a, &b, Strategy::Lookahead, Strategy::Lookahead, seed).unwrap();
        for (index, step) in replay.steps.iter().enumerate().skip(1) {
            let prev = &replay.steps[index - 1];
            let attached_to_active =
                prev.actor == step.actor && prev.description.ends_with("個をバトル場へ");
            // 育てるべき主役（進化済みのメガ）がベンチにいるのに、付けて逃げるのが誤り
            let mega_on_bench = prev.bench[step.actor]
                .iter()
                .any(|p| p.name == "Mega Blaziken ex");
            // 逃げ先がそのメガ（育てたメガを前に出す）なら正しい手順
            let into_mega = step.active[step.actor]
                .as_ref()
                .is_some_and(|p| p.name == "Mega Blaziken ex");
            if attached_to_active
                && mega_on_bench
                && !into_mega
                && step.description.contains("にげる")
            {
                wasted.push(format!(
                    "シード {seed} T{} プレイヤー {}",
                    step.turn, step.actor
                ));
            }
        }
    }
    assert!(wasted.is_empty(), "付けた直後に逃げた: {wasted:?}");
}

#[test]
fn ベンチで進化したばかりのメガにその番のエネルギーを付ける() {
    let a = parse_deck(BLAZIKEN_DECK).unwrap();
    let b = parse_deck(BUTTERFREE_DECK).unwrap();
    let mut wrong = Vec::new();
    for seed in 0..30u64 {
        let replay =
            play_one_game_with_log(&a, &b, Strategy::Lookahead, Strategy::Lookahead, seed).unwrap();
        let mut evolved_bench: Option<(u8, String)> = None;
        for (index, step) in replay.steps.iter().enumerate() {
            if step.actor != 0 {
                continue;
            }
            if let Some(rest) = step
                .description
                .strip_suffix("のポケモンを Mega Blaziken ex に進化")
                && rest.starts_with("ベンチ")
            {
                evolved_bench = Some((step.turn, rest.to_string()));
            }
            if step.description.starts_with("炎エネルギー1個を")
                && let Some((turn, slot)) = &evolved_bench
            {
                let active_is_mega = step.active[0]
                    .as_ref()
                    .is_some_and(|p| p.name == "Mega Blaziken ex");
                let to_other_mega = step.bench[0].iter().enumerate().any(|(i, p)| {
                    p.name == "Mega Blaziken ex"
                        && step.description.contains(&format!("ベンチ{}", i + 1))
                });
                // バトル場がエネルギー 0 なら、そこに付けないとその番はワザを撃てない。
                // 「メガを最速で立てる」と「撃てる番に撃つ」がぶつかる場面は例外にする
                // 盤面は行動後なので、付ける前のエネルギーは 1 つ前の記録で見る
                let active_needs_energy = index > 0
                    && replay.steps[index - 1].active[0]
                        .as_ref()
                        .is_some_and(|p| p.energy.is_empty())
                    && step.description.contains("をバトル場へ");
                // 付けた相手をこの番に前へ出して殴るなら、それも「撃てる番に撃つ」側。
                // 進化したばかりのメガは 0 エネなので、その番は殴れない
                let attacked_with_it = replay.steps[index..]
                    .iter()
                    .take_while(|s| s.turn == step.turn)
                    .any(|s| s.actor == 0 && s.description.contains("がワザ"));
                let moved_up_to_attack = attacked_with_it
                    && replay.steps[index..]
                        .iter()
                        .take_while(|s| s.turn == step.turn)
                        .any(|s| s.description.contains("にげる"));
                if *turn == step.turn
                    && !active_is_mega
                    && !step.description.contains(slot.as_str())
                    && !to_other_mega
                    && !active_needs_energy
                    && !moved_up_to_attack
                {
                    wrong.push(format!(
                        "シード {seed} T{}: {}",
                        step.turn, step.description
                    ));
                }
            }
            if step.description == "ターンを終える" {
                evolved_bench = None;
            }
        }
    }
    assert!(
        wrong.is_empty(),
        "進化したばかりのメガに付けなかった: {wrong:?}"
    );
}

#[test]
fn 引く手があるなら逃げる前に引く() {
    // ユーザーの指摘（バタフリー対バシャーモ seed1 ターン 2）：モノマネむすめを持ったまま
    // 先に逃げてエネルギーを捨て、その後にモノマネむすめを使っていた。
    // 引けばフレイムパッチが来るかもしれない、という見込みで逃げるのが誤り。
    // 引いてから逃げるかどうかを決める
    let a = parse_deck(BUTTERFREE_DECK).unwrap();
    let b = parse_deck(BLAZIKEN_DECK).unwrap();
    let mut wrong = Vec::new();
    for seed in 0..30u64 {
        let replay =
            play_one_game_with_log(&a, &b, Strategy::Lookahead, Strategy::Lookahead, seed).unwrap();
        for (index, step) in replay.steps.iter().enumerate() {
            if !step.description.contains("にげる") {
                continue;
            }
            // 逃げる直前の手札に引く手（ドロー・サーチ）があり、番の続きでそれを使ったなら、
            // 順序が逆
            let before = if index == 0 {
                continue;
            } else {
                &replay.steps[index - 1]
            };
            let hand = &before.hand[step.actor];
            let draw_in_hand = hand.iter().any(|c| {
                matches!(
                    c.name.as_str(),
                    "Professor's Research" | "Copycat" | "Poké Ball"
                )
            });
            let used_later = replay.steps[index + 1..]
                .iter()
                .take_while(|s| s.turn == step.turn && s.actor == step.actor)
                .any(|s| {
                    s.description.contains("Professor's Research")
                        || s.description.contains("Copycat")
                        || s.description.contains("Poké Ball")
                });
            if draw_in_hand && used_later {
                wrong.push(format!(
                    "シード {seed} T{} プレイヤー {}: {}",
                    step.turn, step.actor, step.description
                ));
            }
        }
    }
    assert!(wrong.is_empty(), "引く前に逃げた: {wrong:?}");
}

#[test]
fn 進化できるなら手札を流すドローより先に進化する() {
    // ユーザーの分析（バタフリー対バシャーモの敗因分類）：手札にメガバシャーモ ex と
    // ふしぎなアメがあり、場に前の番からのアチャモがいるのに、先にモノマネむすめを使って
    // 進化パーツを山札に戻していた。「引く手を先に打つ」が、手札を消費する手より
    // 優先されてしまうのが原因。場を整える手は引く手より先に打つ
    let a = parse_deck(BUTTERFREE_DECK).unwrap();
    let b = parse_deck(BLAZIKEN_DECK).unwrap();
    let mut missed = Vec::new();
    let mut chances = 0;
    for seed in 0..40u64 {
        let replay =
            play_one_game_with_log(&a, &b, Strategy::Lookahead, Strategy::Lookahead, seed).unwrap();
        for (index, step) in replay.steps.iter().enumerate() {
            // 後攻の番の開始。ここで場にいるアチャモは前の番からいるので進化できる
            if step.actor != 1 || !step.description.contains("枚引く") || step.turn < 3 {
                continue;
            }
            let hand: Vec<&str> = step.hand[1].iter().map(|c| c.name.as_str()).collect();
            if !hand.contains(&"Mega Blaziken ex") || !hand.contains(&"Rare Candy") {
                continue;
            }
            // ベンチのアチャモに絞る。バトル場での進化は 3 点の的を晒すので、
            // 相手の打点しだいでは進化しない判断もありうる（ユーザーの整理）
            if !step.bench[1].iter().any(|p| p.name == "Torchic") {
                continue;
            }
            chances += 1;
            let evolved = replay.steps[index..]
                .iter()
                .take_while(|s| s.turn == step.turn)
                .any(|s| s.actor == 1 && s.description.contains("Mega Blaziken ex に進化"));
            if !evolved {
                missed.push(format!("シード {seed} T{}", step.turn));
            }
            break;
        }
    }
    assert!(chances > 0, "進化パーツが揃った場面がない");
    assert!(
        missed.is_empty(),
        "揃っているのに進化しなかった: {missed:?}"
    );
}

/// 静的な点数で「無駄」と分かっている手は、先読みの候補に入れない。
///
/// 先読みは 1 手につきロールアウト 1 回で比べるので、評価はノイズだらけ。
/// 静的な点数は同値のときの決着にしか使っていなかったため、
/// **自分のスタジアムをフィールドブロアーで壊す**ような手が通っていた
/// （ユーザーの指摘、`docs/analysis/altaria-vs-blaziken/report-s14.html` の 7〜9 手目）。
#[test]
fn known_useless_actions_are_not_considered() {
    use pocket_engine::lookahead::is_worth_considering;
    use pocket_engine::players::TRAINER_USELESS_VALUE;

    assert!(
        !is_worth_considering(TRAINER_USELESS_VALUE),
        "無駄と分かっている手は候補にしない"
    );
    assert!(
        !is_worth_considering(TRAINER_USELESS_VALUE - 1.0),
        "それより悪い手も候補にしない"
    );
    assert!(is_worth_considering(0.0), "点の付かない手は残す");
    assert!(is_worth_considering(60.0), "価値のある手は残す");
    assert!(
        is_worth_considering(TRAINER_USELESS_VALUE + 1.0),
        "無駄の閾値を上回れば残す"
    );
}

/// 初期配置で、他に選べるたねがあるなら「特性を活かす」ポケモンは候補から外す。
///
/// `l` は点数を見ずロールアウトだけで選ぶので、減点しても挙動が変わらない
/// （修正 3 で静的評価を直したが初手は変わらなかった）。候補から外すことだけが効く。
#[test]
fn bench_role_basics_are_dropped_from_the_setup_choice() {
    use pocket_engine::lookahead::keep_setup_candidates;

    // (この手が「バトル場に出す」か, ベンチ役割か) の並び
    // ダークライ（ベンチ役割）とイーブイ（そうでない）が候補にある
    let both = [(true, true), (true, false)];
    assert_eq!(
        keep_setup_candidates(&both),
        vec![false, true],
        "イーブイだけ残す"
    );

    // ベンチ役割しかいないなら、外すと置けなくなるので残す
    let only_bench_role = [(true, true), (true, true)];
    assert_eq!(
        keep_setup_candidates(&only_bench_role),
        vec![true, true],
        "他に選べないなら残す"
    );

    // バトル場に出す手が混ざっていない候補は触らない
    let others = [(false, true), (false, false)];
    assert_eq!(keep_setup_candidates(&others), vec![true, true]);

    // ベンチ役割がいなければ何も外さない
    let none = [(true, false), (true, false)];
    assert_eq!(keep_setup_candidates(&none), vec![true, true]);
}

/// 気絶したあとの昇格でも、「特性を活かす」ポケモンは候補から外す。
///
/// ユーザーの指摘：「気絶したらダークライではなくププリンを出す。
/// ププリンは逃げエネ 0 なので、一旦出してから考えるのがセオリー」。
///
/// 初期配置は手札から出す `Place`、気絶後の昇格はベンチから出す `Activate` で
/// 別の行動なので、初期配置のフィルタだけでは効かない（実測でダークライが 24%）。
#[test]
fn bench_role_pokemon_are_dropped_from_the_promotion_too() {
    use pocket_engine::lookahead::keep_setup_candidates;

    // 昇格でも同じ判定を使う。(バトル場に出す手か, ベンチ役割か)
    let promotion = [(true, true), (true, false)];
    assert_eq!(
        keep_setup_candidates(&promotion),
        vec![false, true],
        "ベンチ役割でないほうを残す"
    );
}

/// 同じ進化元から複数の進化先が選べるとき、劣るほうを候補から外す。
///
/// ユーザーの指摘：非 ex のビークイン（A2 18）は「ex のワザを無効化するオドリドリ対策」で、
/// それ以外に進化するメリットはない。相手にオドリドリがいなくても 32% で進化していた。
///
/// 打点は ex も非 ex も表記 70 で同じなので、**HP と打点の両方で上回られていれば外す**。
/// 「倒されたときに渡す点数」は判定に入れない（点数を惜しんで選ぶカードではないため）。
#[test]
fn a_dominated_evolution_target_is_dropped() {
    use pocket_engine::lookahead::keep_evolution_candidates;

    // (進化させる場所, 最大打点, 最大 HP)
    // ビークイン ex（140 HP / 70）と ビークイン（100 HP / 70）が同じミツハニーの進化先
    let both = [(0usize, 70u32, 140u32), (0, 70, 100)];
    assert_eq!(
        keep_evolution_candidates(&both),
        vec![true, false],
        "劣るほうを外す"
    );

    // 進化させる場所が違えば比べない
    let different_targets = [(0usize, 70u32, 140u32), (1, 70, 100)];
    assert_eq!(
        keep_evolution_candidates(&different_targets),
        vec![true, true]
    );

    // 打点が高ければ HP が低くても残す（どちらが良いかは先読みに任せる）
    let trade_off = [(0usize, 70u32, 140u32), (0, 90, 100)];
    assert_eq!(keep_evolution_candidates(&trade_off), vec![true, true]);

    // 完全に同じなら両方残す（外すと選べなくなる）
    let same = [(0usize, 70u32, 140u32), (0, 70, 140)];
    assert_eq!(keep_evolution_candidates(&same), vec![true, true]);
}

/// 劣る進化先でも、進化してよい場合がある（ユーザーの補足）。
///
/// 1. 相手の場に ex のワザを無効化する特性がいる（オドリドリの Safeguard）
/// 2. 進化しなければ次の相手のワザで気絶する（HP 管理でやむを得ない）
#[test]
fn a_dominated_evolution_is_allowed_when_it_is_forced() {
    use pocket_engine::lookahead::{blocks_ex_attacks, evolution_is_forced};

    // オドリドリの特性（カード名ではなく効果文で判定する）
    assert!(blocks_ex_attacks(
        "Prevent all damage done to this Pokémon by attacks from your opponent's Pokémon ex."
    ));
    assert!(!blocks_ex_attacks(
        "This Pokémon takes -20 damage from attacks."
    ));

    // 進化しないと倒される（残り 50、相手の打点 60）。進化後 100 なら耐える
    assert!(
        evolution_is_forced(50, 60, 100),
        "進化すれば耐えるなら進化する"
    );
    // 進化しても倒される（残り 50、相手の打点 130）。無理に進化しない
    assert!(
        !evolution_is_forced(50, 130, 100),
        "進化しても倒されるなら意味がない"
    );
    // そもそも耐える（残り 50、相手の打点 40）
    assert!(!evolution_is_forced(50, 40, 100), "耐えるなら急がない");
}

/// 候補どうしは同じ乱数で比べる（共通乱数法）。
///
/// 候補ごとに違うシードを使うと、「A のほうが良い」という判定に手の良し悪しではなく
/// **引きの当たり外れ**が混入する。同じ乱数の下で比べれば、その差が消える。
#[test]
fn candidates_are_compared_under_the_same_randomness() {
    use pocket_engine::lookahead::rollout_seed;

    let base = 12_345_u64;
    // 同じ番の中では、候補が違ってもロールアウト k 回目のシードは同じ
    assert_eq!(rollout_seed(base, 0, 0), rollout_seed(base, 7, 0));
    assert_eq!(rollout_seed(base, 1, 3), rollout_seed(base, 9, 3));
    // ロールアウトの回が違えば別の乱数（平均を取る意味がある）
    assert_ne!(rollout_seed(base, 0, 0), rollout_seed(base, 0, 1));
    // 番が違えば別の乱数
    assert_ne!(rollout_seed(base, 0, 0), rollout_seed(base + 1, 0, 0));
}
