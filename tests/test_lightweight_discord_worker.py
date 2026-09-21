"""Discord Workerの軽量import構成を確認するテスト。"""

# ============================================================
# 標準ライブラリ
# ============================================================

import os
import subprocess
import sys
import unittest
from pathlib import Path


# ============================================================
# パス
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIRECTORY = PROJECT_ROOT / "src"


# ============================================================
# 軽量import
# ============================================================

class LightweightDiscordWorkerTests(unittest.TestCase):
    """Workerが重い更新処理へ依存しないことを確認する。"""

    def test_worker_import_does_not_load_heavy_modules(
        self,
    ) -> None:
        test_code = """
import builtins

original_import = builtins.__import__

blocked_modules = {
    "export_progressive_dividend_candidates",
    "export_database_indicators",
    "update_edinet_financials",
    "pandas",
    "pdfplumber",
    "googleapiclient",
}

def guarded_import(
    name,
    globals=None,
    locals=None,
    fromlist=(),
    level=0,
):
    root_name = name.split(".", 1)[0]

    if root_name in blocked_modules:
        raise AssertionError(
            f"重いモジュールがimportされました: {name}"
        )

    return original_import(
        name,
        globals,
        locals,
        fromlist,
        level,
    )

builtins.__import__ = guarded_import

import discord_candidate_search_worker

assert callable(
    discord_candidate_search_worker.lambda_handler
)
"""

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(
            SRC_DIRECTORY
        )

        result = subprocess.run(
            [
                sys.executable,
                "-c",
                test_code,
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=(
                "Discord Workerの軽量import確認に"
                "失敗しました。\n"
                f"stdout:\n{result.stdout}\n"
                f"stderr:\n{result.stderr}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
