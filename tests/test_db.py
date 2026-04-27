from __future__ import annotations

import sqlite3

import pytest

from upgrade_pyutils.db import query_config_table, query_table


def _build_db(path, rows):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE system_advanced "
            "(adv_id INTEGER PRIMARY KEY, adv_debugkernel INTEGER, adv_isolated_gpu_pci_ids TEXT)"
        )
        for r in rows:
            conn.execute(
                "INSERT INTO system_advanced "
                "(adv_id, adv_debugkernel, adv_isolated_gpu_pci_ids) VALUES (?, ?, ?)",
                r,
            )
        conn.commit()
    finally:
        conn.close()


def test_query_table_returns_rows_with_prefix_stripped(tmp_path):
    db = tmp_path / "freenas-v1.db"
    _build_db(db, [(1, 0, "[]"), (2, 1, '["0000:01:00.0"]')])

    rows = query_table("system_advanced", str(db), "adv_")

    assert rows == [
        {"id": 1, "debugkernel": 0, "isolated_gpu_pci_ids": "[]"},
        {"id": 2, "debugkernel": 1, "isolated_gpu_pci_ids": '["0000:01:00.0"]'},
    ]


def test_query_config_table_returns_first_row(tmp_path):
    db = tmp_path / "freenas-v1.db"
    _build_db(db, [(1, 1, "[]")])

    cfg = query_config_table("system_advanced", str(db), "adv_")

    assert cfg == {"id": 1, "debugkernel": 1, "isolated_gpu_pci_ids": "[]"}


def test_query_table_without_prefix(tmp_path):
    db = tmp_path / "freenas-v1.db"
    _build_db(db, [(1, 0, "[]")])

    rows = query_table("system_advanced", str(db))

    assert rows == [{"adv_id": 1, "adv_debugkernel": 0, "adv_isolated_gpu_pci_ids": "[]"}]


def test_query_config_table_empty_raises_index_error(tmp_path):
    db = tmp_path / "freenas-v1.db"
    _build_db(db, [])

    with pytest.raises(IndexError):
        query_config_table("system_advanced", str(db), "adv_")
