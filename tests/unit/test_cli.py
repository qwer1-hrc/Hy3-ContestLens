from hy3_contestlens.cli.main import parser


def test_parser_reads_default_base_url_from_app_config(tmp_path, monkeypatch) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "app.toml").write_text(
        '[app]\nhost = "127.0.0.1"\nport = 8200\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HY3_CONTESTLENS_ROOT", str(tmp_path))

    args = parser().parse_args(["health"])

    assert args.base_url == "http://127.0.0.1:8200"


def test_parser_allows_explicit_base_url_override(tmp_path, monkeypatch) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "app.toml").write_text(
        '[app]\nhost = "127.0.0.1"\nport = 8200\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("HY3_CONTESTLENS_ROOT", str(tmp_path))

    args = parser().parse_args(["--base-url", "http://localhost:9000", "health"])

    assert args.base_url == "http://localhost:9000"
