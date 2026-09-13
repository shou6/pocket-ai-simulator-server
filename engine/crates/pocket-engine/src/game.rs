//! 1 試合を回すラッパー。
//!
//! deckgym の `Simulation` は使わない。`Simulation::run` は全試合に同じシードを
//! 渡すため、シードを固定すると N 試合が全部同じ試合になってしまう。
//! 複数試合の集計は呼び出し側が試合ごとのシードを与えて行う。

use std::panic::{AssertUnwindSafe, catch_unwind};
use std::str::FromStr;

use deckgym::players::{Player, PlayerCode, create_players};

use crate::lookahead::LookaheadPlayer;
use crate::players::PocketPlayer;
use deckgym::actions::{Action, SimpleAction};
use deckgym::state::GameOutcome;
use deckgym::{Deck, Game, State};
use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};
use thiserror::Error;

/// `ExpectiMinimax` の既定の探索深さ（deckgym の `parse_player_code` と同じ）。
const DEFAULT_EXPECTIMINIMAX_DEPTH: usize = 3;

/// 試合を回せなかった理由。
#[derive(Debug, Error, PartialEq, Eq)]
pub enum GameError {
    /// 方策コードを解釈できなかった。
    #[error("方策コードが不正です: {code}")]
    UnknownStrategy {
        /// 与えられた文字列。
        code: String,
    },

    /// deckgym が試合中に異常終了した。
    ///
    /// 未実装のカードが場に出た場合など、deckgym には panic する箇所がある。
    /// 長時間の探索が 1 試合の異常で落ちないよう、捕まえてエラーとして返す。
    #[error("エンジンが試合中に異常終了しました: {message}")]
    EnginePanic {
        /// panic のメッセージ。
        message: String,
    },
}

/// プレイヤーの方策。deckgym の `PlayerCode` のうち、無人で回せるものだけを扱う。
///
/// 対話式の `h`（`HumanPlayer`）は標準入力を待つため受け付けない。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Strategy {
    /// `r`：合法手から一様ランダムに選ぶ。
    Random,
    /// `aa`：エネルギーを付けて殴るだけ。
    AttachAttack,
    /// `et`：何もせずターンを終える（下限の基準）。
    EndTurn,
    /// `w`：重み付きランダム。
    WeightedRandom,
    /// `v`：評価関数で 1 手先を選ぶ。
    ValueFunction,
    /// `er`：進化を急ぐ。
    EvolutionRusher,
    /// `m`：モンテカルロ木探索。
    Mcts,
    /// `e[:深さ]`：期待値付き minimax。
    ExpectiMinimax {
        /// 探索の深さ。
        max_depth: usize,
    },
    /// `p`：本プロジェクトの独自方策（`crate::players::PocketPlayer`）。
    Pocket,
    /// `l`：`p` を土台に、候補手ごとに番の終わりまで進めた盤面を評価して選ぶ
    /// （`crate::lookahead::LookaheadPlayer`）。
    Lookahead,
}

impl Strategy {
    /// 方策コードの文字列を返す。勝率キャッシュのキーに使う。
    #[must_use]
    pub fn code(self) -> String {
        match self {
            Strategy::Random => "r".to_string(),
            Strategy::AttachAttack => "aa".to_string(),
            Strategy::EndTurn => "et".to_string(),
            Strategy::WeightedRandom => "w".to_string(),
            Strategy::ValueFunction => "v".to_string(),
            Strategy::EvolutionRusher => "er".to_string(),
            Strategy::Mcts => "m".to_string(),
            Strategy::ExpectiMinimax { max_depth } => format!("e:{max_depth}"),
            Strategy::Pocket => "p".to_string(),
            Strategy::Lookahead => "l".to_string(),
        }
    }

    /// deckgym の `PlayerCode` に変換する。独自方策には対応するコードがない。
    fn to_player_code(self) -> Option<PlayerCode> {
        match self {
            Strategy::Random => Some(PlayerCode::R),
            Strategy::AttachAttack => Some(PlayerCode::AA),
            Strategy::EndTurn => Some(PlayerCode::ET),
            Strategy::WeightedRandom => Some(PlayerCode::W),
            Strategy::ValueFunction => Some(PlayerCode::V),
            Strategy::EvolutionRusher => Some(PlayerCode::ER),
            Strategy::Mcts => Some(PlayerCode::M),
            Strategy::ExpectiMinimax { max_depth } => Some(PlayerCode::E { max_depth }),
            Strategy::Pocket | Strategy::Lookahead => None,
        }
    }

    /// この方策のプレイヤーを組み立てる。
    pub(crate) fn build_player(self, deck: Deck) -> Box<dyn Player> {
        let inner: Box<dyn Player> = match self.to_player_code() {
            Some(code) => create_players(deck.clone(), deck, vec![code.clone(), code])
                .into_iter()
                .next()
                .expect("create_players は 2 人分を返す"),
            None if self == Strategy::Lookahead => Box::new(LookaheadPlayer::new(deck)),
            None => Box::new(PocketPlayer::new(deck)),
        };
        Box::new(RuleEnforcingPlayer { inner })
    }
}

impl FromStr for Strategy {
    type Err = GameError;

    fn from_str(code: &str) -> Result<Self, Self::Err> {
        let unknown = || GameError::UnknownStrategy {
            code: code.to_string(),
        };

        if let Some(depth) = code.strip_prefix("e:") {
            let max_depth = depth.parse::<usize>().map_err(|_| unknown())?;
            return Ok(Strategy::ExpectiMinimax { max_depth });
        }

        match code {
            "r" => Ok(Strategy::Random),
            "aa" => Ok(Strategy::AttachAttack),
            "et" => Ok(Strategy::EndTurn),
            "w" => Ok(Strategy::WeightedRandom),
            "v" => Ok(Strategy::ValueFunction),
            "er" => Ok(Strategy::EvolutionRusher),
            "m" => Ok(Strategy::Mcts),
            "e" => Ok(Strategy::ExpectiMinimax {
                max_depth: DEFAULT_EXPECTIMINIMAX_DEPTH,
            }),
            "p" => Ok(Strategy::Pocket),
            "l" => Ok(Strategy::Lookahead),
            _ => Err(unknown()),
        }
    }
}

/// deckgym が実装していないルールを、方策に渡す前に補う。
///
/// ねむり・まひの間はワザとにげるを選べない（公式ルール、`docs/game-rules.md` 6 節）。
/// deckgym `fda48391` はフラグを立てるだけで禁止を実装しておらず
/// （`generate_attack_actions` と `can_retreat` は化石と一部の効果しか見ない）、
/// 眠らされたポケモンがそのまま攻撃・撤退できてしまう。
/// ここでは合法手からそれらを除いてから内側の方策に渡す。
///
/// 状態遷移そのものは deckgym に任せており、ここで直すのは「選べる手」だけ。
pub(crate) struct RuleEnforcingPlayer {
    inner: Box<dyn Player>,
}

impl RuleEnforcingPlayer {
    /// 状態異常で選べない手を除く。除いた結果が空になるなら元のまま返す。
    pub(crate) fn legal_actions(state: &State, actions: &[Action]) -> Vec<Action> {
        let Some(actor) = actions.first().map(|action| action.actor) else {
            return actions.to_vec();
        };
        let locked = state.in_play_pokemon[actor][0]
            .as_ref()
            .is_some_and(|active| active.is_asleep() || active.is_paralyzed());
        if !locked {
            return actions.to_vec();
        }
        let filtered: Vec<Action> = actions
            .iter()
            .filter(|action| {
                !matches!(
                    action.action,
                    SimpleAction::Attack(_) | SimpleAction::Retreat(_)
                )
            })
            .cloned()
            .collect();
        if filtered.is_empty() {
            actions.to_vec()
        } else {
            filtered
        }
    }
}

impl Player for RuleEnforcingPlayer {
    fn decision_fn(
        &mut self,
        rng: &mut StdRng,
        state: &State,
        possible_actions: &[Action],
    ) -> Action {
        let legal = Self::legal_actions(state, possible_actions);
        self.inner.decision_fn(rng, state, &legal)
    }

    fn get_deck(&self) -> Deck {
        self.inner.get_deck()
    }
}

impl std::fmt::Debug for RuleEnforcingPlayer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "RuleEnforcing({:?})", self.inner)
    }
}

/// 準備段階を閉じた瞬間に deckgym が起こす「相手の番の終わり」の特性の発動を取り消す。
///
/// deckgym `fda48391` の `forecast_end_turn` は、両者がバトル場を置き終えると
/// ターン 1 の開始処理として `start_turn_ability_outcomes` を呼ぶ。その中に
/// キャタピーのクイックグロウ「相手の番の終わりに、山札から進化させる」が含まれており、
/// ターン 1 の開始時点で進化の行動なしにトランセルになっていた。準備段階は交互の番ではなく、
/// 相手の番の終わりは存在しない（ユーザーの指摘：バタフリー対バシャーモ seed2）。
///
/// ターン 1 に入った直後、進化の行動なしにバトル場のカードが変わっていたら元に戻し、
/// 進化カードは山札に戻して切り直す（deckgym の処理と同じ順序）。戻したら `true`。
pub(crate) fn undo_setup_phase_evolution(
    before: &State,
    after: &mut State,
    rng: &mut impl Rng,
) -> bool {
    if before.turn_count != 0 || after.turn_count == 0 {
        return false;
    }
    let mut undone = false;
    for player in 0..2 {
        let Some(original) = before.in_play_pokemon[player][0].as_ref() else {
            continue;
        };
        let Some(now) = after.in_play_pokemon[player][0].as_ref() else {
            continue;
        };
        if now.card.get_name() == original.card.get_name() {
            continue;
        }
        let evolution = now.card.clone();
        let mut restored = original.clone();
        // deckgym の `end_turn_maintenance` が公開フィールドに行うことを写す
        restored.played_this_turn = false;
        restored.moved_to_active_this_turn = false;
        restored.ability_used = false;
        after.in_play_pokemon[player][0] = Some(restored);
        after.decks[player].cards.push(evolution);
        after.decks[player].shuffle(false, rng);
        undone = true;
    }
    undone
}

/// 番が変わった直後に残った「この番に出した」印を消す。消したら `true`。
///
/// deckgym `fda48391` は `advance_turn` で `played_this_turn` を落としてから、
/// 「相手の番の終わりに」発動する特性（キャタピーのクイックグロウなど）を適用する。
/// そのため特性で進化したポケモンは印が立ったまま次の番に入り、`move_generation` の
/// `!pokemon.played_this_turn` に弾かれて、その番に進化できなくなる
/// （ユーザーの指摘：バタフリー対バシャーモ seed12 ターン 3。相手の番の終わりに
/// トランセルになったのに、手札のバタフリーへ進化しなかった）。
///
/// 番が始まった直後にはまだ何も場に出していないので、残っている印は前の番のものだけ。
/// まとめて落として構わない。
pub(crate) fn clear_stale_played_this_turn(state: &mut State) -> bool {
    let mut cleared = false;
    for player in 0..2 {
        for pokemon in state.in_play_pokemon[player].iter_mut().flatten() {
            if pokemon.played_this_turn {
                pokemon.played_this_turn = false;
                cleared = true;
            }
        }
    }
    cleared
}

/// その行動で番が終わるか（次の行動から相手の番になる）。
fn ends_turn(action: &Action) -> bool {
    matches!(
        action.action,
        SimpleAction::EndTurn | SimpleAction::Attack(_)
    )
}

/// 行動を 1 つ適用し、準備段階からターン 1 に入るときは [`undo_setup_phase_evolution`] を掛ける。
pub(crate) fn apply_action_with_corrections(
    game: &mut Game<'static>,
    before: &State,
    action: &Action,
    rng: &mut impl Rng,
) {
    game.apply_action(action);
    if before.turn_count == 0 || ends_turn(action) {
        let mut after = game.get_state_clone();
        let mut changed = false;
        if before.turn_count == 0 {
            changed |= undo_setup_phase_evolution(before, &mut after, rng);
        }
        if after.turn_count != before.turn_count {
            changed |= clear_stale_played_this_turn(&mut after);
        }
        if changed {
            game.set_state(after);
        }
    }
}

/// 指定した行動を適用し、番の切り替わりに伴う補正を掛ける。
///
/// [`apply_action_with_corrections`] と同じことをするが、**前の状態の複製を要求しない**。
/// 探索の内側では 1 手ごとに状態をまるごと複製するのが重いので、
/// 準備段階と、番が終わりうる行動の後だけ複製する。
///
/// `last_turn` には行動前のターン番号を渡す。
pub(crate) fn apply_action_at_turn(
    game: &mut Game<'static>,
    last_turn: u8,
    action: &Action,
    rng: &mut impl Rng,
) {
    let in_setup = last_turn == 0;
    let before = if in_setup {
        Some(game.get_state_clone())
    } else {
        None
    };
    game.apply_action(action);
    if !in_setup && !ends_turn(action) {
        return;
    }
    let mut after = game.get_state_clone();
    let mut changed = false;
    if let Some(before) = &before {
        changed |= undo_setup_phase_evolution(before, &mut after, rng);
    }
    if after.turn_count != last_turn {
        changed |= clear_stale_played_this_turn(&mut after);
    }
    if changed {
        game.set_state(after);
    }
}

/// 1 手進め、番の切り替わりに伴う補正を掛ける。
///
/// `last_turn` には直前に見たターン番号を渡す（開始時は 0）。状態の複製は重いので、
/// 準備段階と、番が終わりうる行動（ターンを終える・ワザ）の後だけ前後を見比べる。
pub(crate) fn play_tick_with_corrections(
    game: &mut Game<'static>,
    last_turn: &mut u8,
    rng: &mut impl Rng,
) -> Action {
    let in_setup = *last_turn == 0;
    let before = if in_setup {
        Some(game.get_state_clone())
    } else {
        None
    };
    let action = game.play_tick();
    if !in_setup && !ends_turn(&action) {
        return action;
    }
    let mut after = game.get_state_clone();
    let mut changed = false;
    if let Some(before) = &before {
        changed |= undo_setup_phase_evolution(before, &mut after, rng);
    }
    if after.turn_count != *last_turn {
        changed |= clear_stale_played_this_turn(&mut after);
        *last_turn = after.turn_count;
    }
    if changed {
        game.set_state(after);
    }
    action
}

/// 補正用の乱数。試合のシードから導き、同じシードなら同じ結果になる。
pub(crate) fn correction_rng(seed: u64) -> StdRng {
    StdRng::seed_from_u64(seed ^ 0x5EED_C0DE_0000_0001)
}

/// 1 試合の結果。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MatchResult {
    /// 先攻側（`deck_a`）の勝ち。
    PlayerA,
    /// 後攻側（`deck_b`）の勝ち。
    PlayerB,
    /// 引き分け。
    Tie,
    /// 決着がつかなかった。
    Unfinished,
}

impl From<Option<GameOutcome>> for MatchResult {
    fn from(outcome: Option<GameOutcome>) -> Self {
        match outcome {
            Some(GameOutcome::Win(0)) => MatchResult::PlayerA,
            Some(GameOutcome::Win(_)) => MatchResult::PlayerB,
            Some(GameOutcome::Tie) => MatchResult::Tie,
            None => MatchResult::Unfinished,
        }
    }
}

/// 固定シードで 1 試合を回し、結果を返す。
///
/// `deck_a` が先攻、`deck_b` が後攻。同じ引数なら必ず同じ結果になる。
///
/// # Errors
///
/// deckgym が試合中に panic した場合に [`GameError::EnginePanic`] を返す。
/// 検証済みのデッキ（[`crate::deck::parse_deck`]）を渡していれば通常は起きない。
pub fn play_one_game(
    deck_a: &Deck,
    deck_b: &Deck,
    strategy_a: Strategy,
    strategy_b: Strategy,
    seed: u64,
) -> Result<MatchResult, GameError> {
    let players: Vec<Box<dyn Player>> = vec![
        strategy_a.build_player(deck_a.clone()),
        strategy_b.build_player(deck_b.clone()),
    ];

    // deckgym は未実装カードなどで panic する。1 試合の異常で全体を落とさない
    let outcome = catch_unwind(AssertUnwindSafe(|| {
        let mut game = new_game(players, seed);
        let mut rng = correction_rng(seed);
        let mut last_turn = 0;
        while !game.is_game_over() {
            play_tick_with_corrections(&mut game, &mut last_turn, &mut rng);
        }
        game.get_state_clone().winner
    }))
    .map_err(|payload| GameError::EnginePanic {
        message: panic_message_of(&payload),
    })?;

    Ok(outcome.into())
}

/// `deck_a`（プレイヤー 0）を先攻にして試合を作る。
///
/// deckgym は開始時にコインで先攻を決めるため、引数の順序と先攻が一致しない。
/// 先攻・後攻別の集計とログの意味を保つため、ここで先攻を固定する。
/// 後攻にしたい側は引数の順序を入れ替えて渡す。
pub(crate) fn new_game(players: Vec<Box<dyn Player>>, seed: u64) -> Game<'static> {
    // プレイヤーごとに独立した乱数で初期化する。ひとつの乱数で両方のデッキを
    // シャッフルすると、片方のデッキのカードが 1 枚違うだけで相手の引きまで変わり、
    // 似たデッキどうしを比べられない（差のぶれが勝率のぶれより大きくなっていた）
    let mut game = Game::new_per_player(players, seed, seed ^ 0x5DEE_CE66_D3A1_1B4D);
    let mut state = game.get_state_clone();
    state.current_player = 0;
    game.set_state(state);
    game
}

/// panic のペイロードからメッセージを取り出す。
pub(crate) fn panic_message_of(payload: &Box<dyn std::any::Any + Send>) -> String {
    if let Some(message) = payload.downcast_ref::<&str>() {
        (*message).to_string()
    } else if let Some(message) = payload.downcast_ref::<String>() {
        message.clone()
    } else {
        "詳細不明".to_string()
    }
}
