#!/usr/bin/env python3
"""抓取 https://www.douyin.com/hot 对应的抖音热榜与热点视频详细信息。

两种模式：
  1) --public（推荐免登录）
     - 热榜:  https://aweme.snssdk.com/aweme/v1/hot/search/list/
     - 视频:  https://aweme.snssdk.com/aweme/v1/hot/search/video/list/
     - 无需 Cookie / 签名；仅标准库 + aiohttp
  2) 默认（Cookie 模式）
     - 走本仓库 DouyinAPIClient（www.douyin.com + a_bogus）
     - 可用 --enrich-detail 再拉 aweme/detail

输出（默认 ./Downloaded/hot_page/{timestamp}/）：
  - hot_board.jsonl
  - hot_videos.jsonl
  - summary.json

用法:
  python -m tools.hot_page_dump --public --board-limit 50 --videos-per-topic 3
  python -m tools.hot_page_dump -c config.yml --enrich-detail
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote, urlencode

import aiohttp

from utils.logger import setup_logger

logger = setup_logger("HotPageDump")

DEFAULT_BOARD_LIMIT = 50
DEFAULT_VIDEOS_PER_TOPIC = 3

# Cookie-free public endpoints (no a_bogus / msToken required in practice).
PUBLIC_BOARD_URL = "https://aweme.snssdk.com/aweme/v1/hot/search/list/"
PUBLIC_VIDEO_LIST_URL = "https://aweme.snssdk.com/aweme/v1/hot/search/video/list/"
PUBLIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.douyin.com/",
    "Accept": "application/json, text/plain, */*",
}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dump Douyin /hot board topics and related hot-video details.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yml",
        help="Config file path (default: config.yml); ignored in --public mode",
    )
    parser.add_argument(
        "-p",
        "--path",
        default=None,
        help="Output root (default: config path or ./Downloaded)",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help="Cookie-free mode via aweme.snssdk.com public hot APIs",
    )
    parser.add_argument(
        "--board-limit",
        type=int,
        default=DEFAULT_BOARD_LIMIT,
        help=f"Max hot-board topics (default: {DEFAULT_BOARD_LIMIT})",
    )
    parser.add_argument(
        "--videos-per-topic",
        type=int,
        default=DEFAULT_VIDEOS_PER_TOPIC,
        help=f"Max related videos per topic (default: {DEFAULT_VIDEOS_PER_TOPIC})",
    )
    parser.add_argument(
        "--topic-limit",
        type=int,
        default=0,
        help="Only expand videos for the first N topics (0 = all board items)",
    )
    parser.add_argument(
        "--enrich-detail",
        action="store_true",
        help="Call aweme/detail for each video (Cookie mode only)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.05,
        help="Delay seconds between video-list requests when concurrency=1 (default: 0.05)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Parallel topic video requests (default: 5; use 1 to serialize)",
    )
    return parser.parse_args(argv)


def summarize_board_item(item: Dict[str, Any], rank: int) -> Dict[str, Any]:
    """Normalize a hot-board word_list entry into a stable summary dict."""
    word = str(item.get("word") or item.get("sentence") or "").strip()
    sentence_id = str(item.get("sentence_id") or item.get("group_id") or "").strip()
    hot_value = item.get("hot_value")
    try:
        hot_value_int = int(hot_value) if hot_value is not None else 0
    except (TypeError, ValueError):
        hot_value_int = 0

    hot_url = ""
    if sentence_id and word:
        hot_url = f"https://www.douyin.com/hot/{sentence_id}/{quote(word)}"
    elif sentence_id:
        hot_url = f"https://www.douyin.com/hot/{sentence_id}"

    return {
        "rank": rank,
        "word": word,
        "sentence_id": sentence_id,
        "hot_value": hot_value_int,
        "label": item.get("label"),
        "word_type": item.get("word_type"),
        "view_count": item.get("view_count"),
        "discuss_video_count": item.get("discuss_video_count"),
        "video_count": item.get("video_count"),
        "hot_url": hot_url,
        "raw": item,
    }


def _first_url(url_list: Any) -> str:
    if isinstance(url_list, list):
        for item in url_list:
            if isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def summarize_aweme(
    aweme: Dict[str, Any],
    *,
    rank: int = 0,
    topic_word: str = "",
    topic_sentence_id: str = "",
) -> Dict[str, Any]:
    """Extract commonly used video fields while keeping the raw payload."""
    author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
    stats = aweme.get("statistics") if isinstance(aweme.get("statistics"), dict) else {}
    video = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
    cover = video.get("cover") if isinstance(video.get("cover"), dict) else {}
    play_addr = video.get("play_addr") if isinstance(video.get("play_addr"), dict) else {}
    download_addr = (
        video.get("download_addr") if isinstance(video.get("download_addr"), dict) else {}
    )

    aweme_id = str(aweme.get("aweme_id") or "").strip()
    return {
        "rank": rank,
        "topic_word": topic_word,
        "topic_sentence_id": topic_sentence_id,
        "aweme_id": aweme_id,
        "desc": str(aweme.get("desc") or "").strip(),
        "create_time": aweme.get("create_time"),
        "author_nickname": str(author.get("nickname") or "").strip(),
        "author_uid": str(author.get("uid") or "").strip(),
        "author_sec_uid": str(author.get("sec_uid") or "").strip(),
        "digg_count": stats.get("digg_count"),
        "comment_count": stats.get("comment_count"),
        "share_count": stats.get("share_count"),
        "collect_count": stats.get("collect_count"),
        "play_count": stats.get("play_count"),
        "duration": video.get("duration") or aweme.get("duration"),
        "cover_url": _first_url(cover.get("url_list")),
        "play_url": _first_url(play_addr.get("url_list")),
        "download_url": _first_url(download_addr.get("url_list")),
        "video_url": f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "",
        "raw": aweme,
    }


def _extract_aweme_list(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    candidates: Iterable[Any] = (
        raw.get("aweme_list"),
        (raw.get("data") or {}).get("aweme_list")
        if isinstance(raw.get("data"), dict)
        else None,
    )
    for value in candidates:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _extract_word_list(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    word_list = data.get("word_list") if isinstance(data, dict) else None
    if isinstance(word_list, list):
        return [item for item in word_list if isinstance(item, dict)]
    return []


class PublicHotClient:
    """Cookie-free client for snssdk hot-search endpoints."""

    def __init__(self, proxy: Optional[str] = None, request_timeout: float = 12.0):
        self.proxy = (proxy or "").strip() or None
        self.request_timeout = float(request_timeout or 12.0)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "PublicHotClient":
        # Connect/read caps prevent a single hung socket from freezing the whole run.
        timeout = aiohttp.ClientTimeout(
            total=self.request_timeout,
            connect=min(5.0, self.request_timeout),
            sock_read=min(10.0, self.request_timeout),
        )
        self._session = aiohttp.ClientSession(
            timeout=timeout,
            headers=PUBLIC_HEADERS,
            cookie_jar=aiohttp.DummyCookieJar(),
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        assert self._session is not None
        query = urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        full_url = f"{url}?{query}" if query else url
        try:
            async with self._session.get(full_url, proxy=self.proxy) as response:
                text = await response.text()
                if response.status >= 400:
                    logger.warning("Public API HTTP %s for %s", response.status, url)
                    return {}
                if not text.strip():
                    return {}
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    logger.warning("Public API non-JSON response for %s", url)
                    return {}
                return data if isinstance(data, dict) else {}
        except asyncio.TimeoutError:
            logger.warning("Public API timeout for %s", url)
            return {}
        except aiohttp.ClientError as exc:
            logger.warning("Public API client error for %s: %s", url, exc)
            return {}

    async def get_hot_board(self, *, limit: int) -> List[Dict[str, Any]]:
        raw = await self._get_json(
            PUBLIC_BOARD_URL,
            {
                "aid": "1128",
                "detail_list": "1",
                "count": str(limit or DEFAULT_BOARD_LIMIT),
            },
        )
        items = _extract_word_list(raw)
        if limit and limit > 0:
            items = items[:limit]
        return [summarize_board_item(item, rank=i + 1) for i, item in enumerate(items)]

    async def get_topic_videos(
        self, *, word: str, sentence_id: str, count: int
    ) -> List[Dict[str, Any]]:
        raw = await self._get_json(
            PUBLIC_VIDEO_LIST_URL,
            {
                "aid": "1128",
                "hotword": word,
                "sentence_id": sentence_id,
                "offset": "0",
                "count": str(max(1, int(count or 1))),
                "source": "trending_page",
            },
        )
        return _extract_aweme_list(raw)


async def fetch_hot_board_cookie(
    api_client: Any, *, limit: int
) -> List[Dict[str, Any]]:
    page = await api_client.get_hot_search_board()
    items = list(page.get("items") or [])
    if limit and limit > 0:
        items = items[:limit]
    return [summarize_board_item(item, rank=i + 1) for i, item in enumerate(items)]


async def fetch_topic_videos_cookie(
    api_client: Any,
    *,
    word: str,
    sentence_id: str,
    count: int,
) -> List[Dict[str, Any]]:
    params = await api_client._default_query()
    params.update(
        {
            "hotword": word,
            "offset": 0,
            "count": max(1, int(count or 1)),
            "source": "trending_page",
        }
    )
    if sentence_id:
        params["sentence_id"] = sentence_id

    raw = await api_client._request_json(
        "/aweme/v1/web/hot/search/video/list/",
        params,
        suppress_error=True,
    )
    return _extract_aweme_list(raw)


async def collect_hot_videos(
    *,
    fetch_videos,
    enrich_detail_fn,
    board_items: List[Dict[str, Any]],
    videos_per_topic: int,
    topic_limit: int,
    delay: float,
    concurrency: int = 1,
) -> List[Dict[str, Any]]:
    topics = board_items
    if topic_limit and topic_limit > 0:
        topics = topics[:topic_limit]

    total = len(topics)
    if total == 0:
        return []

    workers = max(1, int(concurrency or 1))
    sem = asyncio.Semaphore(workers)
    results: List[Optional[Dict[str, Any]]] = [None] * total

    async def _one(index: int, topic: Dict[str, Any]) -> None:
        word = str(topic.get("word") or "")
        sentence_id = str(topic.get("sentence_id") or "")
        if not word and not sentence_id:
            results[index] = {"word": word, "sentence_id": sentence_id, "awemes": []}
            return

        async with sem:
            print(
                f"[INFO] videos {index + 1}/{total}: {word or sentence_id}",
                flush=True,
            )
            try:
                awemes = await asyncio.wait_for(
                    fetch_videos(
                        word=word,
                        sentence_id=sentence_id,
                        count=videos_per_topic,
                    ),
                    timeout=20.0,
                )
            except asyncio.TimeoutError:
                print(
                    f"[WARN] timeout topic={word!r} sentence_id={sentence_id}",
                    flush=True,
                )
                awemes = []
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to fetch videos for topic=%r sentence_id=%s: %s",
                    word,
                    sentence_id,
                    exc,
                )
                print(f"[WARN] failed topic={word!r}: {exc}", flush=True)
                awemes = []

            if delay > 0 and workers == 1:
                await asyncio.sleep(delay)

        results[index] = {
            "word": word,
            "sentence_id": sentence_id,
            "awemes": awemes or [],
        }

    await asyncio.gather(
        *[_one(i, topic) for i, topic in enumerate(topics)],
        return_exceptions=False,
    )

    collected: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for item in results:
        if not item:
            continue
        word = str(item.get("word") or "")
        sentence_id = str(item.get("sentence_id") or "")
        awemes = item.get("awemes") or []
        for aweme in awemes[:videos_per_topic]:
            if not isinstance(aweme, dict):
                continue
            aweme_id = str(aweme.get("aweme_id") or "").strip()
            if aweme_id and aweme_id in seen_ids:
                continue
            if aweme_id:
                seen_ids.add(aweme_id)

            detail = aweme
            if enrich_detail_fn and aweme_id:
                try:
                    richer = await enrich_detail_fn(aweme_id)
                    if isinstance(richer, dict) and richer.get("aweme_id"):
                        detail = richer
                except Exception as exc:  # noqa: BLE001
                    logger.debug("detail enrich failed for %s: %s", aweme_id, exc)

            collected.append(
                summarize_aweme(
                    detail,
                    rank=len(collected) + 1,
                    topic_word=word,
                    topic_sentence_id=sentence_id,
                )
            )

    return collected


async def _write_jsonl(path: Path, items: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=False))
            handle.write("\n")


async def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_cookies_from_config(config_path: Path):
    from config import ConfigLoader
    from utils.cookie_utils import sanitize_cookies

    if config_path.exists():
        config = ConfigLoader(str(config_path))
    else:
        config = ConfigLoader()

    cookies = config.get_cookies() or {}
    if cookies:
        return sanitize_cookies(cookies), config

    for candidate in (Path("config/cookies.json"), Path(".cookies.json")):
        if not candidate.exists():
            continue
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict) and raw:
            logger.info("Loaded cookies from %s", candidate)
            return sanitize_cookies(raw), config
    return {}, config


async def run_public(args: argparse.Namespace, output_root: Path, ts: str) -> int:
    if args.enrich_detail:
        print(
            "[WARN] --enrich-detail needs Cookie mode; ignored in --public",
            file=sys.stderr,
        )

    out_dir = output_root / "hot_page" / ts
    async with PublicHotClient() as client:
        print("[INFO] [public] Fetching Douyin hot board (no cookie)...")
        board_items = await client.get_hot_board(limit=int(args.board_limit or 0))
        print(f"[INFO] Hot board items: {len(board_items)}")

        topic_n = len(board_items)
        if args.topic_limit and args.topic_limit > 0:
            topic_n = min(topic_n, int(args.topic_limit))
        print(
            "[INFO] [public] Fetching related hot videos (no cookie)... "
            f"topics={topic_n} concurrency={int(args.concurrency or 1)}",
            flush=True,
        )
        video_items = await collect_hot_videos(
            fetch_videos=client.get_topic_videos,
            enrich_detail_fn=None,
            board_items=board_items,
            videos_per_topic=int(args.videos_per_topic or 1),
            topic_limit=int(args.topic_limit or 0),
            delay=float(args.delay or 0),
            concurrency=int(args.concurrency or 1),
        )
        print(f"[INFO] Hot video items: {len(video_items)}", flush=True)

    return await _persist_outputs(
        out_dir=out_dir,
        ts=ts,
        args=args,
        board_items=board_items,
        video_items=video_items,
        mode="public",
    )


async def run_cookie(args: argparse.Namespace, output_root: Path, ts: str) -> int:
    from core import DouyinAPIClient

    cookies, config = _load_cookies_from_config(Path(args.config))
    if args.path is None and config.get("path"):
        output_root = Path(config.get("path"))
    out_dir = output_root / "hot_page" / ts

    if not cookies:
        logger.warning(
            "No cookies found. Prefer: python -m tools.hot_page_dump --public ... "
            "or run cookie_fetcher first."
        )

    async with DouyinAPIClient(cookies, proxy=config.get("proxy")) as api_client:
        print("[INFO] Fetching Douyin hot board (cookie mode)...")
        board_items = await fetch_hot_board_cookie(
            api_client, limit=int(args.board_limit or 0)
        )
        print(f"[INFO] Hot board items: {len(board_items)}")

        async def _fetch_videos(*, word, sentence_id, count):
            return await fetch_topic_videos_cookie(
                api_client, word=word, sentence_id=sentence_id, count=count
            )

        async def _enrich(aweme_id: str):
            return await api_client.get_video_detail(aweme_id, suppress_error=True)

        print(
            "[INFO] Fetching related hot videos (cookie mode)... "
            f"concurrency={int(args.concurrency or 1)}",
            flush=True,
        )
        video_items = await collect_hot_videos(
            fetch_videos=_fetch_videos,
            enrich_detail_fn=_enrich if args.enrich_detail else None,
            board_items=board_items,
            videos_per_topic=int(args.videos_per_topic or 1),
            topic_limit=int(args.topic_limit or 0),
            delay=float(args.delay or 0),
            concurrency=int(args.concurrency or 1),
        )
        print(f"[INFO] Hot video items: {len(video_items)}", flush=True)

    return await _persist_outputs(
        out_dir=out_dir,
        ts=ts,
        args=args,
        board_items=board_items,
        video_items=video_items,
        mode="cookie",
    )


async def _persist_outputs(
    *,
    out_dir: Path,
    ts: str,
    args: argparse.Namespace,
    board_items: List[Dict[str, Any]],
    video_items: List[Dict[str, Any]],
    mode: str,
) -> int:
    board_path = out_dir / "hot_board.jsonl"
    videos_path = out_dir / "hot_videos.jsonl"
    summary_path = out_dir / "summary.json"

    await _write_jsonl(board_path, board_items)
    await _write_jsonl(videos_path, video_items)
    await _write_json(
        summary_path,
        {
            "source_page": "https://www.douyin.com/hot",
            "mode": mode,
            "fetched_at": ts,
            "board_count": len(board_items),
            "video_count": len(video_items),
            "board_limit": args.board_limit,
            "videos_per_topic": args.videos_per_topic,
            "topic_limit": args.topic_limit,
            "enrich_detail": bool(args.enrich_detail) and mode == "cookie",
            "board_path": str(board_path.resolve()),
            "videos_path": str(videos_path.resolve()),
            "public_apis": {
                "board": PUBLIC_BOARD_URL,
                "video_list": PUBLIC_VIDEO_LIST_URL,
            }
            if mode == "public"
            else None,
            "board_preview": [
                {
                    "rank": item.get("rank"),
                    "word": item.get("word"),
                    "hot_value": item.get("hot_value"),
                    "sentence_id": item.get("sentence_id"),
                    "hot_url": item.get("hot_url"),
                }
                for item in board_items[:10]
            ],
            "video_preview": [
                {
                    "rank": item.get("rank"),
                    "aweme_id": item.get("aweme_id"),
                    "desc": item.get("desc"),
                    "topic_word": item.get("topic_word"),
                    "video_url": item.get("video_url"),
                }
                for item in video_items[:10]
            ],
        },
    )

    print(f"[OK] Hot board -> {board_path.resolve()} ({len(board_items)})")
    print(f"[OK] Hot videos -> {videos_path.resolve()} ({len(video_items)})")
    print(f"[OK] Summary -> {summary_path.resolve()}")
    return 0


async def run(args: argparse.Namespace) -> int:
    output_root = Path(args.path or "./Downloaded")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if args.public:
        return await run_public(args, output_root, ts)
    return await run_cookie(args, output_root, ts)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("[WARN] Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
