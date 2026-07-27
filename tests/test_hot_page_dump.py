"""Unit tests for tools.hot_page_dump summarizers."""

from tools.hot_page_dump import (
    _extract_aweme_list,
    _extract_word_list,
    summarize_aweme,
    summarize_board_item,
)


def test_summarize_board_item_builds_hot_url():
    item = {
        "word": "台风红霞或近期生成",
        "sentence_id": "2579084",
        "hot_value": "11653260",
        "label": 3,
        "discuss_video_count": 12,
    }
    summary = summarize_board_item(item, rank=2)
    assert summary["rank"] == 2
    assert summary["word"] == "台风红霞或近期生成"
    assert summary["sentence_id"] == "2579084"
    assert summary["hot_value"] == 11653260
    assert summary["hot_url"].startswith("https://www.douyin.com/hot/2579084/")
    assert summary["raw"] is item


def test_summarize_aweme_extracts_core_fields():
    aweme = {
        "aweme_id": "123",
        "desc": "hello",
        "author": {"nickname": "n", "uid": "u", "sec_uid": "s"},
        "statistics": {"digg_count": 1, "comment_count": 2},
        "video": {
            "duration": 1000,
            "cover": {"url_list": ["https://cover"]},
            "play_addr": {"url_list": ["https://play"]},
        },
    }
    summary = summarize_aweme(
        aweme, rank=1, topic_word="话题", topic_sentence_id="99"
    )
    assert summary["aweme_id"] == "123"
    assert summary["author_nickname"] == "n"
    assert summary["cover_url"] == "https://cover"
    assert summary["play_url"] == "https://play"
    assert summary["video_url"] == "https://www.douyin.com/video/123"
    assert summary["topic_word"] == "话题"


def test_extract_aweme_list_from_nested_data():
    raw = {"status_code": 0, "data": {"aweme_list": [{"aweme_id": "1"}, "bad"]}}
    items = _extract_aweme_list(raw)
    assert items == [{"aweme_id": "1"}]


def test_extract_word_list_from_snssdk_shape():
    raw = {
        "status_code": 0,
        "data": {"word_list": [{"word": "a", "sentence_id": "1"}, "x"]},
    }
    assert _extract_word_list(raw) == [{"word": "a", "sentence_id": "1"}]
