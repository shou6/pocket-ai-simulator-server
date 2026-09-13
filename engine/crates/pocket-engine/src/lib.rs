//! ポケポケ（Pokémon TCG Pocket）のルールエンジンと対戦シミュレータ。
//!
//! このクレートは純粋な Rust ライブラリであり、Python バインディング
//! （`pocket-engine-py`）や CLI から利用される。
//!
//! 予定しているモジュール構成（`docs/requirements.md` 参照）:
//!
//! - `card`    : カード定義（HP / ワザ / 特性 / 効果）とカードプールの読み込み
//! - `state`   : 対戦状態（盤面・手札・山札・エネルギーゾーン・ポイント）
//! - `rules`   : 合法手の列挙と状態遷移（決定的・シード付き RNG）
//! - `effects` : カード効果の解釈（データ駆動 DSL + 個別実装）
//! - `players` : ランダム / ヒューリスティック / 探索ベースのプレイヤー
//! - `sim`     : 大量対戦の実行とマッチアップ集計

pub mod cards;
pub mod deck;
pub mod game;
pub mod lookahead;
pub mod matchup;
pub mod players;
pub mod replay;

/// `Cargo.toml` が参照している deckgym-core のコミットハッシュ。
/// 公開・デプロイのときに使う固定値で、**開発中に動いている版とは限らない**。
pub const DECKGYM_REVISION: &str = "b2585ade2a331b8463f8a875c8645e114de3a54d";

/// いま動いている deckgym-core のコミットハッシュ。
///
/// 開発中は `engine/Cargo.toml` の `[patch]` が `vendor/deckgym-core` を見るので、
/// [`DECKGYM_REVISION`] の固定値とは中身が違う。勝率のキャッシュと
/// サロゲートの学習データはエンジンが変われば無効になるため、**固定値ではなく
/// 実際に組み込まれた版**を鍵にしないと、古いラベルが黙って使われる
/// （実際に起きた。`docs/analysis/card-pool-expansion.md`）。
///
/// フォークが手元にないとき（git 依存として取り込んだとき）は固定値を返す。
#[must_use]
pub fn deckgym_revision() -> &'static str {
    env!("DECKGYM_BUILD_REVISION")
}

/// エンジンのバージョン。勝率キャッシュのキーに含め、
/// エンジン更新時に古い結果と混ざらないようにする。
#[must_use]
pub fn engine_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_version_matches_cargo_manifest() {
        assert_eq!(engine_version(), "0.1.0");
    }

    /// `DECKGYM_REVISION` が Cargo.toml の `rev` と食い違わないことを保証する。
    /// deckgym を更新したときの直し忘れを防ぐ。
    #[test]
    fn deckgym_revision_matches_cargo_manifest() {
        let manifest = include_str!("../Cargo.toml");
        let rev = manifest
            .lines()
            .find_map(|line| line.split_once("rev = \""))
            .and_then(|(_, rest)| rest.split('"').next())
            .expect("Cargo.toml に deckgym の rev があるはず");
        assert_eq!(DECKGYM_REVISION, rev);
    }

    /// 手元のフォークを見てビルドしているあいだは、実際に動いている版を返す。
    /// 固定値を返すと、エンジンを直しても古い勝率のラベルが有効なままになる。
    #[test]
    fn deckgym_revision_follows_the_local_fork() {
        let fork =
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../vendor/deckgym-core");
        if !fork.join(".git").exists() {
            return; // フォークが無い環境では固定値でよい
        }
        let head = std::process::Command::new("git")
            .args(["-C", fork.to_str().expect("パス"), "rev-parse", "HEAD"])
            .output()
            .expect("git rev-parse が動くはず");
        let head = String::from_utf8(head.stdout)
            .expect("UTF-8")
            .trim()
            .to_string();
        assert_eq!(
            deckgym_revision(),
            head,
            "手元のフォークの HEAD と一致するはず"
        );
    }
}
