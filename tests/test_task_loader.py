"""Tests for config.task_loader — separate task.yml records."""

from pathlib import Path

import pytest
import yaml

from config import ConfigLoader, build_task_config, iter_task_jobs, load_task_records


def _write_task(tmp_path: Path, payload) -> Path:
    path = tmp_path / "task.yml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_load_task_records_from_tasks_key(tmp_path):
    path = _write_task(
        tmp_path,
        {
            "tasks": [
                {"name": "a", "link": "https://www.douyin.com/video/1"},
                {
                    "name": "b",
                    "link": [
                        "https://www.douyin.com/video/2",
                        "https://www.douyin.com/video/3",
                    ],
                    "mode": ["post"],
                    "number": {"post": 5},
                },
            ]
        },
    )
    records = load_task_records(str(path))
    assert len(records) == 2
    assert records[0]["name"] == "a"
    assert records[0]["link"] == ["https://www.douyin.com/video/1"]
    assert records[1]["link"] == [
        "https://www.douyin.com/video/2",
        "https://www.douyin.com/video/3",
    ]
    assert records[1]["overrides"]["mode"] == ["post"]
    assert records[1]["overrides"]["number"] == {"post": 5}


def test_load_task_records_from_top_level_list(tmp_path):
    path = _write_task(
        tmp_path,
        [{"link": "https://www.douyin.com/user/abc", "mode": ["like"]}],
    )
    records = load_task_records(str(path))
    assert len(records) == 1
    assert records[0]["link"] == ["https://www.douyin.com/user/abc"]
    assert records[0]["overrides"]["mode"] == ["like"]


def test_load_task_records_skips_missing_link(tmp_path):
    path = _write_task(
        tmp_path,
        {
            "tasks": [
                {"name": "empty"},
                {"name": "ok", "urls": "https://www.douyin.com/video/9"},
            ]
        },
    )
    records = load_task_records(str(path))
    assert len(records) == 1
    assert records[0]["name"] == "ok"
    assert records[0]["link"] == ["https://www.douyin.com/video/9"]


def test_build_task_config_applies_overrides_without_mutating_base(tmp_path):
    base = ConfigLoader(None)
    base.update(path=str(tmp_path), link=["https://www.douyin.com/video/base"], mode=["post"])
    record = {
        "name": "t1",
        "link": ["https://www.douyin.com/video/task"],
        "overrides": {"mode": ["like"], "number": {"like": 3}},
    }
    task_cfg = build_task_config(base, record)
    assert task_cfg.get_links() == ["https://www.douyin.com/video/task"]
    assert task_cfg.get("mode") == ["like"]
    assert task_cfg.get("number")["like"] == 3
    # Base untouched
    assert base.get_links() == ["https://www.douyin.com/video/base"]
    assert base.get("mode") == ["post"]


def test_iter_task_jobs(tmp_path):
    path = _write_task(
        tmp_path,
        {
            "tasks": [
                {"name": "one", "link": "https://www.douyin.com/video/1"},
                {"name": "two", "link": ["https://www.douyin.com/video/2"]},
            ]
        },
    )
    base = ConfigLoader(None)
    base.update(path=str(tmp_path))
    jobs = iter_task_jobs(base, str(path))
    assert [name for name, _cfg, _urls in jobs] == ["one", "two"]
    assert jobs[0][2] == ["https://www.douyin.com/video/1"]
    assert jobs[1][2] == ["https://www.douyin.com/video/2"]


def test_load_task_records_missing_file():
    with pytest.raises(FileNotFoundError):
        load_task_records("definitely-missing-task-file.yml")


def test_validate_can_skip_links_requirement(tmp_path):
    config = ConfigLoader(None)
    config.update(path=str(tmp_path), link=[])
    assert config.validate() is False
    assert config.validate(require_links=False) is True


def test_load_direct_video_mode_with_link(tmp_path):
    path = _write_task(
        tmp_path,
        {
            "tasks": [
                {
                    "name": "clips",
                    "mode": "video",
                    "link": [
                        "https://www.douyin.com/video/1",
                        "https://www.douyin.com/note/2",
                    ],
                }
            ]
        },
    )
    records = load_task_records(str(path))
    assert len(records) == 1
    assert records[0]["direct_mode"] == "video"
    assert records[0]["link"] == [
        "https://www.douyin.com/video/1",
        "https://www.douyin.com/note/2",
    ]
    # video mode must not pollute ConfigLoader.mode (user strategies)
    assert "mode" not in records[0]["overrides"]


def test_load_video_shorthand_key(tmp_path):
    path = _write_task(
        tmp_path,
        {
            "tasks": [
                {"video": "https://www.douyin.com/video/9"},
                {
                    "name": "batch",
                    "video": [
                        "https://www.douyin.com/video/1",
                        "https://www.douyin.com/gallery/2",
                    ],
                },
            ]
        },
    )
    records = load_task_records(str(path))
    assert len(records) == 2
    assert records[0]["direct_mode"] == "video"
    assert records[0]["link"] == ["https://www.douyin.com/video/9"]
    assert records[1]["name"] == "batch"
    assert records[1]["link"] == [
        "https://www.douyin.com/video/1",
        "https://www.douyin.com/gallery/2",
    ]


def test_build_task_config_direct_video_keeps_base_user_mode(tmp_path):
    base = ConfigLoader(None)
    base.update(path=str(tmp_path), mode=["post"])
    record = {
        "name": "clips",
        "link": ["https://www.douyin.com/video/1"],
        "direct_mode": "video",
        "overrides": {},
    }
    task_cfg = build_task_config(base, record)
    assert task_cfg.get_links() == ["https://www.douyin.com/video/1"]
    assert task_cfg.get("mode") == ["post"]  # unused for video URLs; left as base
