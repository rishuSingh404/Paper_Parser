"""Ingestion sources. Each module implements `fetch(since, params) -> Iterable[RawPaper]`
and is toggled by `config.sources[<name>].enabled`.
"""
