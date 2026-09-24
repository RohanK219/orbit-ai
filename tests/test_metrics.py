import unittest

from orbit.metrics import Measurement, MetricsCollector


class MetricsTests(unittest.TestCase):
    def test_latency_arithmetic(self):
        measurement = Measurement(2, 0.7, 0.3, 0.5, 1.2)
        self.assertEqual(measurement.time_to_first_text, 0.8)
        self.assertEqual(measurement.perceived_latency, 1.5)
        self.assertEqual(measurement.time_to_complete, 2.2)

    def test_cost_arithmetic(self):
        metrics = MetricsCollector("gpt-4o-mini-transcribe", "gpt-4o-mini")
        metrics.add(Measurement(60, 0, 0, 0, 0, transcript_chars=400, answer_chars=800))
        expected = 0.003 + (100 / 1_000_000) * 0.15 + (200 / 1_000_000) * 0.60
        self.assertAlmostEqual(metrics.estimated_cost_usd(), expected)
        self.assertGreater(metrics.projected_hourly_cost_usd(), 0)


if __name__ == "__main__":
    unittest.main()
