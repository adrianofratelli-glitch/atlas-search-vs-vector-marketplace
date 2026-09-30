"""Unit tests for observability.py's Metrics — pure in-memory logic, no network."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observability import Metrics


class TestMetrics(unittest.TestCase):
    def test_observe_increments_request_count(self):
        m = Metrics()
        m.observe("/search", 200, 12.5)
        m.observe("/search", 200, 7.5)
        snap = m.snapshot()
        self.assertEqual(snap["routes"]["/search"]["requests"], 2)
        self.assertEqual(snap["routes"]["/search"]["avg_latency_ms"], 10.0)
        self.assertEqual(snap["routes"]["/search"]["max_latency_ms"], 12.5)

    def test_5xx_counted_as_error(self):
        m = Metrics()
        m.observe("/agent", 500, 20)
        m.observe("/agent", 200, 5)
        self.assertEqual(m.snapshot()["routes"]["/agent"]["errors_5xx"], 1)

    def test_4xx_not_counted_as_error(self):
        m = Metrics()
        m.observe("/search", 422, 3)
        self.assertEqual(m.snapshot()["routes"]["/search"]["errors_5xx"], 0)

    def test_bump_business_counter(self):
        m = Metrics()
        m.bump("agent_tool_call")
        m.bump("agent_tool_call", 2)
        self.assertEqual(m.snapshot()["counters"]["agent_tool_call"], 3)


if __name__ == "__main__":
    unittest.main()
