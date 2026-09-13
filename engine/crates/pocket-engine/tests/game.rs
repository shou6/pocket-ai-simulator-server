//! 1 試合を回すラッパーのテスト。

use pocket_engine::deck::{Deck, parse_deck};
use pocket_engine::game::{GameError, MatchResult, Strategy, play_one_game};

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
fn 同じシードなら同じ結果になる() {
    let (a, b) = (fire(), fire());
    let first = play_one_game(&a, &b, Strategy::Random, Strategy::Random, 42).unwrap();
    let second = play_one_game(&a, &b, Strategy::Random, Strategy::Random, 42).unwrap();
    assert_eq!(first, second);
}

#[test]
fn シードが違えば結果が分かれる() {
    // ミラーマッチをシードを変えて回すと、両者に勝ちが出るはず。
    // 方策 aa（エネルギーを付けて殴る）は決着がつく。deckgym fda48391 で確認
    let (a, b) = (fire(), fire());
    let results: Vec<MatchResult> = (0..40)
        .map(|seed| {
            play_one_game(&a, &b, Strategy::AttachAttack, Strategy::AttachAttack, seed).unwrap()
        })
        .collect();

    assert!(results.contains(&MatchResult::PlayerA), "先攻の勝ちがない");
    assert!(results.contains(&MatchResult::PlayerB), "後攻の勝ちがない");
}

#[test]
fn 対戦は必ず終了する() {
    // 決着しないまま無限に進む状態を作らないことの確認。
    // deckgym は 30 ターンを超えると引き分けにするので、Tie も終了扱いとする
    let (a, b) = (fire(), fire());
    for seed in 0..30 {
        for strategy in [Strategy::Random, Strategy::AttachAttack] {
            let result = play_one_game(&a, &b, strategy, strategy, seed).unwrap();
            assert_ne!(
                result,
                MatchResult::Unfinished,
                "シード {seed} で決着がつかなかった"
            );
        }
    }
}

#[test]
fn ランダム同士はほとんど三十ターンで引き分けになる() {
    // 30 ターン経過での引き分けは公式ルール（docs/game-rules.md 10 節）。
    // 方策 r は攻撃が噛み合わないため、ミラーマッチはほとんど引き分けになる。
    //
    // 「すべて引き分け」と書いていたが、それは当時の乱数に依存した経験則だった。
    // プレイヤーごとに独立した乱数で初期化するようにしたら 20 件中 1 件が決着した
    // （正当な結果の変化）。規則そのものは「引き分けが大半を占める」ことで確かめる。
    let (a, b) = (fire(), fire());
    let results: Vec<MatchResult> = (0..20)
        .map(|seed| play_one_game(&a, &b, Strategy::Random, Strategy::Random, seed).unwrap())
        .collect();
    let ties = results.iter().filter(|r| **r == MatchResult::Tie).count();
    assert!(ties >= 15, "引き分けが {ties} 件しかない: {results:?}");
}

#[test]
fn 方策コードを解釈できる() {
    assert_eq!("r".parse::<Strategy>().unwrap(), Strategy::Random);
    assert_eq!("aa".parse::<Strategy>().unwrap(), Strategy::AttachAttack);
    assert_eq!(
        "e".parse::<Strategy>().unwrap(),
        Strategy::ExpectiMinimax { max_depth: 3 }
    );
    assert_eq!(
        "e:2".parse::<Strategy>().unwrap(),
        Strategy::ExpectiMinimax { max_depth: 2 }
    );
}

#[test]
fn 対話用の方策は受け付けない() {
    // deckgym の "h"（HumanPlayer）は標準入力を待つのでサーバでは使えない
    assert!(matches!(
        "h".parse::<Strategy>(),
        Err(GameError::UnknownStrategy { .. })
    ));
}

#[test]
fn 未知の方策コードはエラーになる() {
    assert!(matches!(
        "zzz".parse::<Strategy>(),
        Err(GameError::UnknownStrategy { .. })
    ));
}

#[test]
fn 方策コードを文字列に戻せる() {
    // 勝率キャッシュのキーに使うため、往復できる必要がある
    for code in ["r", "aa", "et", "w", "v", "er", "m", "e:3", "e:5"] {
        let strategy: Strategy = code.parse().unwrap();
        assert_eq!(strategy.code(), code);
    }
}

#[test]
fn エンジンの異常終了を結果として返す() {
    // deckgym には未実装カードなどで panic する箇所がある。
    // 長時間の探索が 1 試合の panic で落ちないよう、捕まえてエラーにする。
    // ここではデッキの検証を通さずに直接組み立てて確かめる
    let deck =
        pocket_engine::deck::parse_deck_unchecked(&FIRE_DECK.replace("2 A1 042", "2 B2 092"))
            .expect("枚数などは正しいので組み立てられるはず");
    let result = play_one_game(
        &deck,
        &deck,
        Strategy::AttachAttack,
        Strategy::AttachAttack,
        1,
    );
    assert!(
        matches!(result, Err(GameError::EnginePanic { .. }) | Ok(_)),
        "panic がそのまま伝播している"
    );
}

/// ねむり・まひ中はワザとにげるを選べない（公式ルール、`docs/game-rules.md` 6 節）。
///
/// deckgym はフラグを立てるだけで禁止を実装していないため、ラッパーが方策に渡す前に除く。
#[test]
fn 眠っている間はワザもにげるも選ばれない() {
    use pocket_engine::replay::play_one_game_with_log;

    // ねむり技を持つデッキ（エーフィ・チルット・プリン・ダークライ）
    let sleeper = parse_deck(
        "\
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
",
    )
    .expect("眠りデッキは有効なはず");
    let fighter = parse_deck(
        "\
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
",
    )
    .expect("ルカリオデッキは有効なはず");

    let mut violations = 0;
    let mut asleep_turns = 0;
    for seed in 0..30u64 {
        for strategy in [Strategy::Pocket, Strategy::AttachAttack] {
            let replay = play_one_game_with_log(&fighter, &sleeper, strategy, strategy, seed)
                .expect("対戦できるはず");
            for (index, step) in replay.steps.iter().enumerate() {
                if index == 0 || step.actor != 0 {
                    continue;
                }
                let Some(active) = replay.steps[index - 1].active[0].as_ref() else {
                    continue;
                };
                let locked = active.status.iter().any(|s| s == "ねむり" || s == "まひ");
                if !locked {
                    continue;
                }
                asleep_turns += 1;
                if step.description.contains(" がワザ") || step.description.contains("にげる")
                {
                    violations += 1;
                }
            }
        }
    }
    assert!(asleep_turns > 0, "眠らされた場面が一度もない");
    assert_eq!(
        violations, 0,
        "眠り・まひ中にワザかにげるを選んだ回数: {violations}"
    );
}
