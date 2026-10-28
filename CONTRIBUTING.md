# Contributing

## 開発の準備

```bash
git clone https://github.com/nokgc333/frame-slicer.git && cd frame-slicer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install          # 任意(black・isort・flake8・gitleaks)
```

依存を変えたら、ロックファイルも更新する(CI が `uv lock --check` で確かめる)。

```bash
uv lock
```

## 変更の流れ

1. `main` から、作業ブランチを切る(`feat/` `fix/` `refactor/` `test/` `docs/` `chore/` + ケバブケース)
2. **テストを先に書く**(RED → GREEN → REFACTOR)。新機能・バグ修正には、必ずテストを付ける
3. 次をすべて通す(CI と同じ。カバレッジは 100% を保つ)

   ```bash
   black --check . && isort --check . && flake8 frame_slicer tests && mypy frame_slicer && vulture frame_slicer vulture_whitelist.py
   pytest --cov-fail-under=100
   ```

4. コミットは、[Conventional Commits](https://www.conventionalcommits.org/) の形式で、説明は英語・小文字の命令形・末尾にピリオドなし(例: `feat: add a husk option`)
5. `main` へは、PR で取り込む(CI が通ってから、マージコミットで)

## CI の構成

- `.github/workflows/ci.yml` は、再利用できるワークフロー `python-checks.yml` を呼ぶだけ。lint、3 OS(Ubuntu / macOS / Windows)× Python 3.11〜3.13 のテスト、ロックファイルの一致、ビルドした wheel を別のフォルダで動かす確認、を行う。
- 他のリポジトリからも `uses: nokgc333/frame-slicer/.github/workflows/python-checks.yml@<ref>` で呼べる。
- `release.yml` は、`v*` のタグで動く。タグが `pyproject.toml` の版と一致し、`CHANGELOG.md` にその版の記載があることを確かめて、GitHub Releases に wheel と sdist を載せる。

## リリース

1. `pyproject.toml` の版と、`CHANGELOG.md` の見出しを更新して、PR で取り込む。
2. `main` に `vX.Y.Z` のタグを付けて push する(公開されるので、本人が行う)。

PyPI への公開(Trusted Publishing)は、まだ設定していない。
