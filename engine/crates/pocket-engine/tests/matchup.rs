//! 相性評価（N 試合の集計）のテスト。

use pocket_engine::deck::{Deck, parse_deck};
use pocket_engine::game::Strategy;
use pocket_engine::matchup::{
    MatchupError, Outcome, Record, evaluate_matchup, replay_matchup_game,
};

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

fn evaluate(a: &Deck, b: &Deck, games: u32, seed: u64) -> pocket_engine::matchup::Matchup {
    evaluate_matchup(
        a,
        b,
        Strategy::AttachAttack,
        Strategy::AttachAttack,
        games,
        seed,
    )
    .expect("評価できるはず")
}

#[test]
fn 指定した試合数だけ対戦する() {
    let matchup = evaluate(&fire(), &water(), 40, 1);
    assert_eq!(matchup.overall().games(), 40);
}

#[test]
fn 先攻と後攻を半分ずつ入れ替える() {
    let matchup = evaluate(&fire(), &water(), 40, 1);
    assert_eq!(matchup.going_first.games(), 20);
    assert_eq!(matchup.going_second.games(), 20);
}

#[test]
fn 試合数が奇数でも先攻が一試合多いだけで済む() {
    let matchup = evaluate(&fire(), &water(), 41, 1);
    assert_eq!(matchup.going_first.games(), 21);
    assert_eq!(matchup.going_second.games(), 20);
}

#[test]
fn 同じシードなら同じ結果になる() {
    let (a, b) = (fire(), water());
    let first = evaluate(&a, &b, 40, 7);
    let second = evaluate(&a, &b, 40, 7);
    assert_eq!(first.going_first, second.going_first);
    assert_eq!(first.going_second, second.going_second);
}

#[test]
fn シードが違えば結果が変わる() {
    let (a, b) = (fire(), water());
    let first = evaluate(&a, &b, 40, 1);
    let second = evaluate(&a, &b, 40, 2);
    assert_ne!(
        (first.going_first, first.going_second),
        (second.going_first, second.going_second)
    );
}

#[test]
fn 試合数が零ならエラーになる() {
    assert_eq!(
        evaluate_matchup(
            &fire(),
            &water(),
            Strategy::AttachAttack,
            Strategy::AttachAttack,
            0,
            1,
        ),
        Err(MatchupError::NoGames)
    );
}

#[test]
fn ミラーマッチの勝率は五割付近になる() {
    // 構造的な妥当性の確認（docs/requirements.md 4.3 節）
    let matchup = evaluate(&fire(), &fire(), 200, 99);
    let rate = matchup
        .overall()
        .win_rate()
        .expect("決着した試合があるはず");
    assert!(
        (0.40..=0.60).contains(&rate),
        "ミラーマッチの勝率が偏っている: {rate}"
    );
}

#[test]
fn 結果に方策コードとリビジョンが入る() {
    // 勝率キャッシュのキーに使う
    let matchup = evaluate_matchup(
        &fire(),
        &water(),
        Strategy::AttachAttack,
        Strategy::Random,
        10,
        1,
    )
    .unwrap();
    assert_eq!(matchup.strategy_a, "aa");
    assert_eq!(matchup.strategy_b, "r");
    assert_eq!(matchup.seed, 1);
    assert_eq!(
        matchup.deckgym_revision,
        pocket_engine::deckgym_revision(),
        "いま動いている deckgym の版が記録されていない"
    );
}

#[test]
fn 引き分けは半勝として数える() {
    let record = Record {
        wins: 3,
        losses: 5,
        ties: 2,
        unfinished: 0,
    };
    assert_eq!(record.games(), 10);
    assert!((record.win_rate().unwrap() - 0.4).abs() < 1e-9);
}

#[test]
fn 決着した試合がなければ勝率は求まらない() {
    let record = Record {
        wins: 0,
        losses: 0,
        ties: 0,
        unfinished: 5,
    };
    assert_eq!(record.win_rate(), None);
}

#[test]
fn 信頼区間は勝率を挟み零から一の範囲に収まる() {
    let record = Record {
        wins: 30,
        losses: 20,
        ties: 0,
        unfinished: 0,
    };
    let rate = record.win_rate().unwrap();
    let (low, high) = record.confidence_interval_95().unwrap();
    assert!(0.0 <= low && low < rate, "下限が不正: {low}");
    assert!(rate < high && high <= 1.0, "上限が不正: {high}");
}

#[test]
fn 試合数が増えると信頼区間は狭くなる() {
    let small = Record {
        wins: 30,
        losses: 20,
        ties: 0,
        unfinished: 0,
    };
    let large = Record {
        wins: 300,
        losses: 200,
        ties: 0,
        unfinished: 0,
    };
    let (sl, sh) = small.confidence_interval_95().unwrap();
    let (ll, lh) = large.confidence_interval_95().unwrap();
    assert!(lh - ll < sh - sl, "試合数を増やしても狭まっていない");
}

// --- 1 試合ずつの結果と、その再現 ------------------------------------------

#[test]
fn 試合ごとの結果を試合の順に返す() {
    let matchup = evaluate(&fire(), &water(), 21, 3);
    assert_eq!(matchup.outcomes.len(), 21);
    // 前半（奇数なら 1 試合多い）が評価対象デッキの先攻
    let (first, second) = matchup.outcomes.split_at(11);
    let count = |games: &[Outcome], outcome: Outcome| {
        u32::try_from(games.iter().filter(|o| **o == outcome).count()).unwrap()
    };
    assert_eq!(count(first, Outcome::Win), matchup.going_first.wins);
    assert_eq!(count(first, Outcome::Loss), matchup.going_first.losses);
    assert_eq!(count(second, Outcome::Win), matchup.going_second.wins);
    assert_eq!(count(second, Outcome::Tie), matchup.going_second.ties);
}

#[test]
fn 集計した試合をログ付きで同じ流れに再現できる() {
    for strategy in [Strategy::AttachAttack, "l".parse().expect("方策 l")] {
        let matchup =
            evaluate_matchup(&fire(), &water(), strategy, strategy, 8, 5).expect("評価できるはず");
        for index in 0..8 {
            let game = replay_matchup_game(&fire(), &water(), strategy, strategy, 8, 5, index)
                .expect("再現できるはず");
            assert_eq!(game.a_is_first, index < 4, "{index} 試合目の先攻");
            assert_eq!(
                game.outcome, matchup.outcomes[index as usize],
                "{index} 試合目の結果が集計と違う"
            );
        }
    }
}

#[test]
fn 範囲外の試合は再現しない() {
    let err = replay_matchup_game(
        &fire(),
        &water(),
        Strategy::AttachAttack,
        Strategy::AttachAttack,
        8,
        5,
        8,
    )
    .unwrap_err();
    assert_eq!(err, MatchupError::GameOutOfRange { index: 8, games: 8 });
}
