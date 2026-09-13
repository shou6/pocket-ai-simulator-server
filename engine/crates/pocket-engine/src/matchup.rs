//! 2 デッキの相性評価。N 試合を並列に回して勝率と信頼区間を求める。
//!
//! deckgym の `Simulation` は使わない。`Simulation::run` は全試合に同じシードを
//! 渡すため、シードを固定すると N 試合が全部同じ試合になってしまう。
//! ここではマスターシードから試合ごとのシードを導出し、自前で並列化する。

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use rayon::prelude::*;
use thiserror::Error;

use crate::deck::Deck;
use crate::game::{GameError, MatchResult, Strategy, play_one_game};

/// 95% 信頼区間に使う標準正規分布の分位点。
const Z_95: f64 = 1.959_963_984_540_054;

/// 相性を評価できなかった理由。
#[derive(Debug, Error, PartialEq, Eq)]
pub enum MatchupError {
    /// 試合数に 0 が指定された。
    #[error("試合数は 1 以上である必要があります")]
    NoGames,

    /// 試合の実行に失敗した。
    ///
    /// 途中経過は返さない。一部の試合だけ落ちた集計は勝率を歪めるため。
    #[error(transparent)]
    Engine(#[from] GameError),
}

/// ある座席（先攻または後攻）での成績。すべて評価対象デッキから見た値。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Record {
    /// 勝ち。
    pub wins: u32,
    /// 負け。
    pub losses: u32,
    /// 引き分け（30 ターン経過、または同時に 3 ポイント到達）。
    pub ties: u32,
    /// 決着がつかなかった試合。通常は 0 になる。
    pub unfinished: u32,
}

impl Record {
    /// 試合数。
    #[must_use]
    pub fn games(&self) -> u32 {
        self.wins + self.losses + self.ties + self.unfinished
    }

    /// 決着した試合数。勝率と信頼区間の母数になる。
    #[must_use]
    pub fn decided(&self) -> u32 {
        self.wins + self.losses + self.ties
    }

    /// 勝ち点。引き分けは 0.5 勝として数える（`docs/game-rules.md` 11 節）。
    #[must_use]
    pub fn score(&self) -> f64 {
        f64::from(self.wins) + f64::from(self.ties) * 0.5
    }

    /// 勝率。決着した試合がなければ `None`。
    #[must_use]
    pub fn win_rate(&self) -> Option<f64> {
        let decided = self.decided();
        if decided == 0 {
            return None;
        }
        Some(self.score() / f64::from(decided))
    }

    /// 勝率の 95% 信頼区間。決着した試合がなければ `None`。
    #[must_use]
    pub fn confidence_interval_95(&self) -> Option<(f64, f64)> {
        self.confidence_interval(Z_95)
    }

    /// 勝率の信頼区間を Wilson score interval で求める。
    ///
    /// 引き分けを 0.5 勝として数えるため厳密には二項分布ではないが、
    /// 正規近似より端点での挙動が素直なので Wilson を使う。
    #[must_use]
    pub fn confidence_interval(&self, z: f64) -> Option<(f64, f64)> {
        let n = f64::from(self.decided());
        if n == 0.0 {
            return None;
        }
        let p = self.score() / n;
        let z2 = z * z;
        let denominator = 1.0 + z2 / n;
        let center = p + z2 / (2.0 * n);
        let margin = z * (p * (1.0 - p) / n + z2 / (4.0 * n * n)).sqrt();
        Some((
            ((center - margin) / denominator).max(0.0),
            ((center + margin) / denominator).min(1.0),
        ))
    }

    /// 1 試合の結果を加算する。
    fn add(&mut self, result: MatchResult, is_first: bool) {
        // 評価対象デッキから見た結果に直す
        let won = match result {
            MatchResult::PlayerA => Some(is_first),
            MatchResult::PlayerB => Some(!is_first),
            MatchResult::Tie | MatchResult::Unfinished => None,
        };
        match (won, result) {
            (Some(true), _) => self.wins += 1,
            (Some(false), _) => self.losses += 1,
            (None, MatchResult::Tie) => self.ties += 1,
            (None, _) => self.unfinished += 1,
        }
    }

    /// 2 つの成績を合算する。
    fn merge(mut self, other: Self) -> Self {
        self.wins += other.wins;
        self.losses += other.losses;
        self.ties += other.ties;
        self.unfinished += other.unfinished;
        self
    }
}

/// 相性評価の結果。
///
/// 勝率キャッシュのキーに使えるよう、方策コードと deckgym のリビジョンを含む。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Matchup {
    /// 評価対象デッキが先攻だった試合の成績。
    pub going_first: Record,
    /// 評価対象デッキが後攻だった試合の成績。
    pub going_second: Record,
    /// 評価対象デッキ側の方策コード。
    pub strategy_a: String,
    /// 相手デッキ側の方策コード。
    pub strategy_b: String,
    /// 使ったマスターシード。
    pub seed: u64,
    /// 対戦に使った deckgym のコミットハッシュ。
    pub deckgym_revision: &'static str,
}

impl Matchup {
    /// 先攻後攻を合算した成績。
    #[must_use]
    pub fn overall(&self) -> Record {
        self.going_first.merge(self.going_second)
    }
}

/// 2 デッキを `games` 試合対戦させ、相性を返す。
///
/// 先攻後攻は半分ずつ入れ替える。試合数が奇数のときは `deck_a` の先攻が 1 試合多くなる。
/// 試合ごとのシードはマスターシードから決定的に導出するので、同じ引数なら必ず同じ結果になる。
///
/// # Errors
///
/// `games` が 0 のとき [`MatchupError::NoGames`] を返す。
pub fn evaluate_matchup(
    deck_a: &Deck,
    deck_b: &Deck,
    strategy_a: Strategy,
    strategy_b: Strategy,
    games: u32,
    seed: u64,
) -> Result<Matchup, MatchupError> {
    if games == 0 {
        return Err(MatchupError::NoGames);
    }

    // 試合ごとのシードを先に決めておく。並列実行の順序に結果が左右されないようにするため
    let mut rng = StdRng::seed_from_u64(seed);
    let seeds: Vec<u64> = (0..games).map(|_| rng.r#gen()).collect();

    // 前半は deck_a が先攻、後半は deck_b が先攻。奇数なら deck_a の先攻が 1 試合多い
    let first_half = games.div_ceil(2) as usize;

    let (going_first, going_second) = seeds
        .par_iter()
        .enumerate()
        .map(
            |(index, &game_seed)| -> Result<(Record, Record), MatchupError> {
                let a_is_first = index < first_half;
                let result = if a_is_first {
                    play_one_game(deck_a, deck_b, strategy_a, strategy_b, game_seed)?
                } else {
                    play_one_game(deck_b, deck_a, strategy_b, strategy_a, game_seed)?
                };

                let mut record = Record::default();
                record.add(result, a_is_first);
                Ok(if a_is_first {
                    (record, Record::default())
                } else {
                    (Record::default(), record)
                })
            },
        )
        .try_reduce(
            || (Record::default(), Record::default()),
            |acc: (Record, Record), item: (Record, Record)| {
                Ok((acc.0.merge(item.0), acc.1.merge(item.1)))
            },
        )?;

    Ok(Matchup {
        going_first,
        going_second,
        strategy_a: strategy_a.code(),
        strategy_b: strategy_b.code(),
        seed,
        deckgym_revision: crate::deckgym_revision(),
    })
}
