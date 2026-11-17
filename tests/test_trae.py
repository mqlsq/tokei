import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from test_codex_limits import USAGE


def _row(seq, typ, data, metadata=None):
    row = {"seq": seq, "type": typ, "data": json.dumps(data)}
    if metadata is not None:
        row["metadata"] = json.dumps(metadata)
    return row


def _token_usage(seq, prompt, completion, reasoning, cache_read, cache_write, when):
    return _row(seq, "token_usage", {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "total_tokens": prompt + completion,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }, metadata={"created_at": int(when.timestamp() * 1000)})


class TraeScanTests(unittest.TestCase):
    def write_task(self, root, task_id, rows):
        task_dir = Path(root) / "TraeCode" / "uid1"
        task_dir.mkdir(parents=True, exist_ok=True)
        path = task_dir / f"{task_id}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n{broken\n",
                        encoding="utf-8")
        return path

    def scan(self, root, tool="trae_cn", cache=None):
        scan_cache = cache if cache is not None else {"v": USAGE._SCAN_CACHE_VERSION}
        env_name = USAGE._TRAE_DIRS[tool][1]
        with mock.patch.dict(os.environ, {env_name: str(root)}):
            result = USAGE.scan_trae(USAGE.range_bounds(), scan_cache, tool=tool)
        return result, scan_cache

    def test_token_usage_aggregates_and_dedupes(self):
        now = datetime.now().astimezone().replace(minute=5, second=0, microsecond=0)
        with tempfile.TemporaryDirectory() as tmp:
            self.write_task(tmp, "task-a", [
                _row(1, "model_config", {"model_name": "qwen3.8-flash__dev"}),
                _token_usage(2, 12000, 800, 300, 9000, 1000, now),
                # 同 seq 重放，必须去重
                _token_usage(2, 999999, 999, 999, 0, 0, now),
                _token_usage(3, 5000, 200, 0, 4000, 0, now),
                _row(4, "timing_events", {"x": 1}),
            ])
            result, cache = self.scan(tmp)

        usage = result["ranges"]["all"]
        # in = prompt - cr - cw
        self.assertEqual(usage["in"], (12000 - 9000 - 1000) + (5000 - 4000))
        self.assertEqual(usage["out"], 1000)
        self.assertEqual(usage["cr"], 13000)
        self.assertEqual(usage["cw"], 1000)
        self.assertEqual(usage["reason"], 300)
        self.assertEqual(usage["sessions"], {"task-a"})
        models = usage["models"]
        self.assertEqual(set(models), {"qwen3.8-flash"})
        self.assertEqual(models["qwen3.8-flash"]["in"], 3000)
        entry = cache["trae_cn"][f"{tmp}/TraeCode/uid1/task-a.jsonl"]
        day = entry["days"][now.date().isoformat()]
        self.assertEqual(day["hours"][now.hour],
                         day["in"] + day["out"] + day["cr"] + day["cw"] + day["reason"])
        self.assertEqual(entry["model"], "qwen3.8-flash")

    def test_cache_deletion_keeps_ledger_totals(self):
        now = datetime.now().astimezone()
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_task(tmp, "task-b", [
                _token_usage(1, 1000, 100, 0, 0, 0, now),
            ])
            first, cache = self.scan(tmp)
            self.assertEqual(first["ranges"]["all"]["in"], 1000)

            # 任务结束后 Trae 删除缓存文件：账本兜底，历史不缩水
            path.unlink()
            second, _ = self.scan(tmp, cache=cache)
            self.assertEqual(second["ranges"]["all"]["in"], 1000)
            self.assertEqual(second["ranges"]["all"]["sessions"], {"task-b"})

            # 文件重建并带全量历史：同任务快照整体替换而非累加
            self.write_task(tmp, "task-b", [
                _token_usage(1, 1000, 100, 0, 0, 0, now),
                _token_usage(2, 2000, 200, 0, 0, 0, now),
            ])
            third, _ = self.scan(tmp, cache=cache)
            self.assertEqual(third["ranges"]["all"]["in"], 3000)

    def test_ledger_flush_urgent_survives_process_restart(self):
        """事件文件易失 + 每轮采集独立进程：账本变化必须立即落盘，
        不能被 ledger_flush 的当日节流（600s）推迟到文件删除之后。"""
        now = datetime.now().astimezone()
        with tempfile.TemporaryDirectory() as tmp:
            ledger_path = Path(tmp) / "ledger.json"
            path = self.write_task(tmp, "task-e", [
                _token_usage(1, 1000, 100, 0, 0, 0, now),
            ])
            backup = dict(USAGE._LEDGER_CACHE)
            try:
                USAGE._LEDGER_CACHE.update(
                    {"data": None, "dirty": False, "urgent": False})
                with mock.patch.object(USAGE, "_LEDGER_FILE", str(ledger_path)), \
                        mock.patch.object(USAGE, "_load_tokei_config", lambda: {}):
                    first, _ = self.scan(tmp)
                    self.assertEqual(first["ranges"]["all"]["in"], 1000)
                    # 本轮账本有变化：必须已标记 urgent，绕过当日节流
                    self.assertTrue(USAGE._LEDGER_CACHE["urgent"])
                    USAGE.ledger_flush()
                    self.assertTrue(ledger_path.exists())

                    # 模拟下一轮采集是全新进程：内存账本清空、事件文件已删
                    USAGE._LEDGER_CACHE.update(
                        {"data": None, "dirty": False, "urgent": False})
                    path.unlink()
                    second, _ = self.scan(tmp)
                    self.assertEqual(second["ranges"]["all"]["in"], 1000)
                    self.assertEqual(second["ranges"]["all"]["sessions"], {"task-e"})
            finally:
                USAGE._LEDGER_CACHE.clear()
                USAGE._LEDGER_CACHE.update(backup)

    def test_two_tasks_share_day(self):
        now = datetime.now().astimezone()
        with tempfile.TemporaryDirectory() as tmp:
            self.write_task(tmp, "task-c", [_token_usage(1, 100, 10, 0, 0, 0, now)])
            self.write_task(tmp, "task-d", [_token_usage(1, 200, 20, 0, 0, 0, now)])
            result, _ = self.scan(tmp)
        usage = result["ranges"]["all"]
        self.assertEqual(usage["in"], 300)
        self.assertEqual(usage["sessions"], {"task-c", "task-d"})

    def test_missing_directory_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = self.scan(Path(tmp) / "absent")
        self.assertEqual(result["ranges"]["all"]["in"], 0)
        self.assertEqual(result["ranges"]["all"]["sessions"], set())


if __name__ == "__main__":
    unittest.main()
