"""通过公开 M.func 候选流接口测试实际 Lua 滤镜，不连接用户词库。

需要隔离环境中的 lupa（使用 Lua 5.4）。
"""

from pathlib import Path
import sys
import unittest

from lupa.lua54 import LuaRuntime


FILTER_PATH = Path(__file__).resolve().parents[1] / "lua" / "frequency_filter.lua"


class FrequencyFilterTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.filter = self.lua.execute(FILTER_PATH.read_text(encoding="utf-8"))
        self.run_filter = self.lua.eval("""
            function(filter, candidates, counts, first_only)
                local reads = 0
                local input = {
                    iter = function(self)
                        return function()
                            reads = reads + 1
                            return candidates[reads]
                        end
                    end
                }
                local output = {}
                local worker = coroutine.create(function()
                    yield = coroutine.yield
                    filter.func(input, { counts = counts })
                end)
                while coroutine.status(worker) ~= "dead" do
                    local ok, candidate = coroutine.resume(worker)
                    assert(ok, candidate)
                    if candidate then
                        output[#output + 1] = candidate
                        if first_only then break end
                    end
                end
                return output, reads
            end
        """)

    def apply(self, candidates, counts=None, first_only=False):
        stream = self.lua.table_from(
            [self.lua.table_from(candidate) for candidate in candidates]
        )
        output, reads = self.run_filter(
            self.filter, stream, self.lua.table_from(counts or {}), first_only
        )
        return [output[i] for i in range(1, len(output) + 1)], reads

    def test_lima_full_match_precedes_frequent_partial_match(self):
        output, _ = self.apply(
            [
                {"text": "立马", "start": 0, "_end": 4},
                {"text": "里", "start": 0, "_end": 2},
            ],
            {"里": 19, "立马": 3},
        )
        self.assertEqual([candidate.text for candidate in output], ["立马", "里"])

    def test_equal_coverage_uses_frequency_not_character_length(self):
        output, _ = self.apply(
            [
                {"text": "西安", "start": 0, "_end": 4},
                {"text": "先", "start": 0, "_end": 4},
            ],
            {"先": 19, "西安": 3},
        )
        self.assertEqual([candidate.text for candidate in output], ["先", "西安"])

    def test_equal_coverage_and_frequency_preserve_upstream_order(self):
        output, _ = self.apply(
            [
                {"text": "立马", "start": 0, "_end": 4},
                {"text": "利马", "start": 0, "_end": 4},
                {"text": "立码", "start": 0, "_end": 4},
            ],
            {"立马": 3, "利马": 3, "立码": 3},
        )
        self.assertEqual(
            [candidate.text for candidate in output], ["立马", "利马", "立码"]
        )

    def test_coverage_is_end_minus_start_not_absolute_end(self):
        output, _ = self.apply(
            [
                {"text": "里", "start": 5, "_end": 7},
                {"text": "立马", "start": 3, "_end": 7},
            ],
            {"里": 19, "立马": 3},
        )
        self.assertEqual([candidate.text for candidate in output], ["立马", "里"])

    def test_candidates_after_first_64_remain_in_original_order(self):
        candidates = [
            {"text": f"候选{i}", "start": 0, "_end": 4}
            for i in range(1, 71)
        ]
        output, _ = self.apply(candidates, {"候选64": 5, "候选70": 100})
        self.assertEqual(
            [candidate.text for candidate in output],
            ["候选64"]
            + [f"候选{i}" for i in range(1, 64)]
            + [f"候选{i}" for i in range(65, 71)],
        )

    def test_first_result_does_not_exhaust_candidate_stream(self):
        candidates = [
            {"text": f"候选{i}", "start": 0, "_end": 4}
            for i in range(1, 1001)
        ]
        output, reads = self.apply(candidates, first_only=True)
        self.assertEqual([candidate.text for candidate in output], ["候选1"])
        self.assertEqual(reads, 64)


if __name__ == "__main__":
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    unittest.main(verbosity=2)
