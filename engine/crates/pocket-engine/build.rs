//! ビルド時に、いま組み込む deckgym-core の版を決める。
//!
//! 開発中は `engine/Cargo.toml` の `[patch]` が `vendor/deckgym-core` を見るので、
//! `Cargo.toml` の `rev` は実際に動いている版と一致しない。勝率のキャッシュと
//! サロゲートの学習データはエンジンが変われば無効になるため、固定値を鍵にすると
//! 古いラベルが黙って使われる（`docs/analysis/card-pool-expansion.md`）。

use std::path::Path;
use std::process::Command;

fn main() {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    let fork = Path::new(&manifest_dir).join("../../../vendor/deckgym-core");

    // フォークのコミットが変われば作り直す
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=Cargo.toml");
    for rel in ["HEAD", "refs/heads/pocket-cards"] {
        let path = fork.join(".git").join(rel);
        if path.exists() {
            println!("cargo:rerun-if-changed={}", path.display());
        }
    }

    let revision = local_fork_revision(&fork).unwrap_or_else(|| pinned_revision(&manifest_dir));
    println!("cargo:rustc-env=DECKGYM_BUILD_REVISION={revision}");
}

/// 手元のフォークの HEAD。フォークが無い（git 依存として取り込んだ）なら `None`。
fn local_fork_revision(fork: &Path) -> Option<String> {
    if !fork.join(".git").exists() {
        return None;
    }
    let output = Command::new("git")
        .args(["-C", fork.to_str()?, "rev-parse", "HEAD"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let revision = String::from_utf8(output.stdout).ok()?.trim().to_string();
    (!revision.is_empty()).then_some(revision)
}

/// `Cargo.toml` に書いてある `rev`。
fn pinned_revision(manifest_dir: &str) -> String {
    let manifest = std::fs::read_to_string(Path::new(manifest_dir).join("Cargo.toml"))
        .expect("Cargo.toml を読めるはず");
    manifest
        .lines()
        .find_map(|line| line.split_once("rev = \""))
        .and_then(|(_, rest)| rest.split('"').next())
        .expect("Cargo.toml に deckgym の rev があるはず")
        .to_string()
}
