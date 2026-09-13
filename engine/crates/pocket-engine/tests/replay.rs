//! 対戦ログのテスト。
//!
//! 勝敗だけでは方策の良し悪しを判断できないため、盤面の推移を記録する。

use pocket_engine::deck::{Deck, parse_deck};
use pocket_engine::game::Strategy;
use pocket_engine::replay::play_one_game_with_log;

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

fn fire() -> Deck {
    parse_deck(FIRE_DECK).expect("炎デッキは有効なはず")
}

#[test]
fn 対戦の行動をすべて記録する() {
    let replay = play_one_game_with_log(
        &fire(),
        &fire(),
        Strategy::AttachAttack,
        Strategy::AttachAttack,
        1,
    )
    .expect("対戦できるはず");

    assert!(
        replay.steps.len() > 10,
        "行動が少なすぎる: {}",
        replay.steps.len()
    );
    assert!(
        replay.steps.iter().any(|step| step.turn >= 2),
        "ターンが進んでいない"
    );
}

#[test]
fn 各行動に盤面の状態が付く() {
    let replay = play_one_game_with_log(
        &fire(),
        &fire(),
        Strategy::AttachAttack,
        Strategy::AttachAttack,
        1,
    )
    .expect("対戦できるはず");

    // 最初のセットアップが終われば、両者にバトルポケモンがいる。
    // 決着時は負けた側のバトル場が空になるので、中盤の盤面で確かめる
    let middle = &replay.steps[replay.steps.len() / 2];
    let active = middle.active[0].as_ref().expect("先攻のバトル場が空");
    assert!(!active.name.is_empty());
    assert!(active.max_hp > 0, "最大 HP が取れていない");
    assert!(middle.active[1].is_some(), "後攻のバトル場が空");
    assert!(middle.deck_size[0] < 20, "山札が減っていない");
}

#[test]
fn 行動の説明が日本語で入る() {
    let replay = play_one_game_with_log(
        &fire(),
        &fire(),
        Strategy::AttachAttack,
        Strategy::AttachAttack,
        1,
    )
    .expect("対戦できるはず");

    assert!(
        replay
            .steps
            .iter()
            .any(|step| step.description.contains("ワザ")),
        "ワザを使った記録がない"
    );
    assert!(
        replay
            .steps
            .iter()
            .any(|step| step.description.contains("エネルギー")),
        "エネルギーを付けた記録がない"
    );
}

#[test]
fn 同じシードなら同じログになる() {
    let a =
        play_one_game_with_log(&fire(), &fire(), Strategy::Pocket, Strategy::Pocket, 7).unwrap();
    let b =
        play_one_game_with_log(&fire(), &fire(), Strategy::Pocket, Strategy::Pocket, 7).unwrap();
    assert_eq!(a.steps.len(), b.steps.len());
    assert_eq!(a.result, b.result);
}

#[test]
fn 勝敗が記録される() {
    let replay = play_one_game_with_log(
        &fire(),
        &fire(),
        Strategy::AttachAttack,
        Strategy::AttachAttack,
        1,
    )
    .expect("対戦できるはず");
    // 3 ポイントに到達するのは片方だけ（両者同時到達は引き分けだが稀）
    assert!(replay.points.iter().filter(|p| **p >= 3).count() <= 1);
    assert!(replay.turns > 0, "ターンが進んでいない");
}

#[test]
fn 記録つきの対戦と通常の対戦は同じ結果になる() {
    // 記録の有無で結果が変わるなら、記録側の経路にバグがある
    use pocket_engine::game::play_one_game;

    let a = fire();
    let b = fire();
    for seed in 0..20u64 {
        for strategy in [Strategy::Pocket, Strategy::AttachAttack] {
            let plain = play_one_game(&a, &b, strategy, strategy, seed).unwrap();
            let logged = play_one_game_with_log(&a, &b, strategy, strategy, seed).unwrap();
            assert_eq!(
                plain, logged.result,
                "シード {seed} 方策 {strategy:?} で食い違う"
            );
        }
    }
}

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
fn 内部処理の行動も日本語で説明する() {
    // ベンチへのダメージ・回復・どうぐ・スタジアムなどは deckgym が別の行動として
    // 処理する。英語の内部表記のままだと効いていないように読めてしまう
    let blaziken = parse_deck(BLAZIKEN_DECK).expect("バシャーモデッキは有効なはず");
    let butterfree = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");

    let mut bench_damage_seen = false;
    for seed in 1..=8u64 {
        let replay = play_one_game_with_log(
            &butterfree,
            &blaziken,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        for step in &replay.steps {
            let looks_like_debug = step.description.contains(" { ")
                || step.description.contains("_idx")
                || step.description.starts_with("Use")
                || step.description.starts_with("Apply");
            assert!(
                !looks_like_debug,
                "シード {seed} で内部表記のまま: {}",
                step.description
            );
            if step.description.contains("ベンチ") && step.description.contains("ダメージ") {
                bench_damage_seen = true;
            }
        }
    }
    assert!(
        bench_damage_seen,
        "ブーバーのベンチへのダメージが記録されていない"
    );
}

#[test]
fn 先攻は常にデッキaで最初の行動から記録する() {
    // deckgym は開始時にコインで先攻を決めるが、先攻・後攻別の集計と
    // ログの `先`/`後` の意味を保つため、deck_a を必ず先攻にする
    let blaziken = parse_deck(BLAZIKEN_DECK).expect("バシャーモデッキは有効なはず");
    let butterfree = parse_deck(BUTTERFREE_DECK).expect("バタフリーデッキは有効なはず");
    for seed in 1..=20u64 {
        let replay = play_one_game_with_log(
            &butterfree,
            &blaziken,
            Strategy::Pocket,
            Strategy::Pocket,
            seed,
        )
        .expect("対戦できるはず");
        let first = &replay.steps[0];
        assert_eq!(
            first.actor, 0,
            "シード {seed} で後攻が先に動いた: {}",
            first.description
        );
        assert_eq!(first.turn, 0, "最初の行動はターン 0 のはず");

        // 準備が終わって最初に引くのも先攻
        let draw = replay
            .steps
            .iter()
            .find(|step| step.description.contains("枚引く"))
            .expect("引く行動がない");
        assert_eq!(draw.actor, 0, "シード {seed} で後攻が先に引いた");
        assert_eq!(draw.turn, 1, "最初に引くのはターン 1 のはず");

        // 「ターンを終える」はそのターンの側に付く（次のターンの見出しに混ざらない）
        let end = replay
            .steps
            .iter()
            .find(|step| step.description == "ターンを終える")
            .expect("ターンを終える行動がない");
        assert_eq!(end.turn, 0, "準備の終了はターン 0 のはず");
    }
}

#[test]
fn 手札の内容を記録する() {
    // たねを出せなかったのが「引けなかった」のか「出さなかった」のかを
    // ログで判別できるように、初手と各行動後の手札を残す
    let replay = play_one_game_with_log(&fire(), &fire(), Strategy::Pocket, Strategy::Pocket, 1)
        .expect("対戦できるはず");

    for hand in &replay.opening_hands {
        assert_eq!(hand.len(), 5, "初手は 5 枚のはず: {hand:?}");
    }
    for step in &replay.steps {
        assert_eq!(step.hand[0].len(), step.hand_size[0]);
        assert_eq!(step.hand[1].len(), step.hand_size[1]);
    }
    // 引いた直後の手札は 1 枚増えている
    let draw = replay
        .steps
        .iter()
        .position(|step| step.description.contains("枚引く"))
        .expect("引く行動がない");
    let before = &replay.steps[draw - 1];
    let after = &replay.steps[draw];
    assert_eq!(
        after.hand[after.actor].len(),
        before.hand[after.actor].len() + 1
    );
}

#[test]
fn 準備段階の終わりに特性で勝手に進化しない() {
    // ユーザーの指摘（バタフリー対バシャーモ seed2）：キャタピーの特性クイックグロウ
    // 「相手の番の終わりに」が準備段階の終わりに発火し、ターン 1 の開始時点で
    // 進化の行動なしにトランセルになっていた。準備段階は交互の番ではないので、
    // 相手の番の終わりは存在しない。ターン 1 の最初の行動の時点で、バトル場は
    // 準備段階の終わりと同じカードでなければならない
    let a = parse_deck(BUTTERFREE_DECK).unwrap();
    let b = parse_deck(BLAZIKEN_DECK).unwrap();
    let mut caterpie_starts = 0;
    let mut wrong = Vec::new();
    for seed in 0..12u64 {
        let replay =
            play_one_game_with_log(&a, &b, Strategy::Lookahead, Strategy::Lookahead, seed).unwrap();
        // 準備段階を閉じる「ターンを終える」の記録は行動後の状態なので、その 1 つ前と比べる
        let Some(setup_close) = replay.steps.iter().rposition(|s| s.turn == 0) else {
            continue;
        };
        let setup_end = setup_close - 1;
        let Some(turn1_start) = replay.steps.iter().position(|s| s.turn == 1) else {
            continue;
        };
        for player in 0..2 {
            let before = replay.steps[setup_end].active[player]
                .as_ref()
                .map(|p| p.name.clone());
            let after = replay.steps[turn1_start].active[player]
                .as_ref()
                .map(|p| p.name.clone());
            if before.as_deref() == Some("Caterpie") {
                caterpie_starts += 1;
            }
            if before != after {
                wrong.push(format!(
                    "シード {seed} プレイヤー {player}: {before:?} → {after:?}"
                ));
            }
        }
    }
    assert!(
        caterpie_starts > 0,
        "キャタピーが準備段階でバトル場に出た試合がない"
    );
    assert!(wrong.is_empty(), "準備段階の終わりに進化した: {wrong:?}");
}

#[test]
fn 特性で進化したポケモンは次の自分の番に進化できる() {
    // ユーザーの指摘（バタフリー対バシャーモ seed12 ターン 3）：相手の番の終わりに
    // キャタピーが特性でトランセルになったのに、手札にバタフリーがあるのに進化しなかった。
    // deckgym は advance_turn（played_this_turn のリセット）の後に「相手の番の終わり」の
    // 特性を適用するため、進化したポケモンが「この番に出したばかり」の扱いで次の番に入る
    let a = parse_deck(BUTTERFREE_DECK).unwrap();
    let b = parse_deck(BLAZIKEN_DECK).unwrap();
    let mut stuck = Vec::new();
    let mut chances = 0;
    for seed in 0..20u64 {
        let replay =
            play_one_game_with_log(&a, &b, Strategy::Lookahead, Strategy::Lookahead, seed).unwrap();
        for (index, step) in replay.steps.iter().enumerate() {
            // 自分の番の最初のドロー時点で、バトル場がトランセル・手札にバタフリー
            if step.actor != 0 || !step.description.contains("枚引く") {
                continue;
            }
            let on_metapod = step.active[0].as_ref().is_some_and(|p| p.name == "Metapod");
            let has_butterfree = step.hand[0].iter().any(|c| c.name == "Butterfree");
            if !on_metapod || !has_butterfree {
                continue;
            }
            chances += 1;
            // 手札からの進化のほかに、そうじゅくエキスで進化することもある。
            // 手段を問わず結果で見る
            let evolved = replay.steps[index..]
                .iter()
                .take_while(|s| s.turn == step.turn)
                .any(|s| s.active[0].as_ref().is_some_and(|p| p.name == "Butterfree"));
            if !evolved {
                stuck.push(format!("シード {seed} T{}", step.turn));
            }
        }
    }
    assert!(chances > 0, "トランセル + 手札のバタフリーの場面がない");
    assert!(
        stuck.is_empty(),
        "進化できる場面で進化しなかった: {stuck:?}"
    );
}
