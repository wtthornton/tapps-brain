"""Tests for the optional OpenTelemetry exporter (STORY-007.5)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tapps_brain.metrics import MetricsCollector, MetricsSnapshot


class TestOTelExporter:
    """Test OTelExporter with a mocked OTel meter."""

    def test_export_counters(self) -> None:
        mock_meter = MagicMock()
        mock_counter = MagicMock()
        mock_meter.create_counter.return_value = mock_counter

        from tapps_brain.otel_exporter import OTelExporter

        exporter = OTelExporter(meter=mock_meter)

        collector = MetricsCollector()
        collector.increment("store.save", 5)
        snapshot = collector.snapshot()

        exporter.export(snapshot)

        mock_meter.create_counter.assert_called_once()
        mock_counter.add.assert_called_once_with(5)

    def test_export_histograms(self) -> None:
        mock_meter = MagicMock()
        mock_histogram = MagicMock()
        mock_meter.create_histogram.return_value = mock_histogram

        from tapps_brain.otel_exporter import OTelExporter

        exporter = OTelExporter(meter=mock_meter)

        collector = MetricsCollector()
        collector.observe("store.save_ms", 1.5)
        collector.observe("store.save_ms", 2.5)
        snapshot = collector.snapshot()

        exporter.export(snapshot)

        mock_meter.create_histogram.assert_called_once()
        mock_histogram.record.assert_called_once_with(2.0)  # mean of 1.5 and 2.5

    def test_export_mixed(self) -> None:
        mock_meter = MagicMock()
        mock_counter = MagicMock()
        mock_histogram = MagicMock()
        mock_meter.create_counter.return_value = mock_counter
        mock_meter.create_histogram.return_value = mock_histogram

        from tapps_brain.otel_exporter import OTelExporter

        exporter = OTelExporter(meter=mock_meter)

        collector = MetricsCollector()
        collector.increment("store.get", 3)
        collector.observe("store.get_ms", 0.5)
        snapshot = collector.snapshot()

        exporter.export(snapshot)

        mock_counter.add.assert_called_once_with(3)
        mock_histogram.record.assert_called_once()

    def test_lazy_counter_creation(self) -> None:
        mock_meter = MagicMock()
        mock_counter = MagicMock()
        mock_meter.create_counter.return_value = mock_counter

        from tapps_brain.otel_exporter import OTelExporter

        exporter = OTelExporter(meter=mock_meter)

        snapshot = MetricsSnapshot(counters={"a": 1, "b": 2})
        exporter.export(snapshot)
        assert mock_meter.create_counter.call_count == 2

        # Second export reuses existing counters
        exporter.export(snapshot)
        assert mock_meter.create_counter.call_count == 2


class TestCreateExporter:
    """Test create_exporter() graceful behavior."""

    def test_returns_none_without_otel(self) -> None:
        from tapps_brain.otel_exporter import create_exporter

        with patch("tapps_brain.otel_exporter.feature_flags") as ff:
            ff.otel = False
            result = create_exporter()
            assert result is None

    def test_returns_exporter_with_mock_meter(self) -> None:
        from tapps_brain.otel_exporter import OTelExporter, create_exporter

        with patch("tapps_brain.otel_exporter.feature_flags") as ff:
            ff.otel = True
            result = create_exporter(meter=MagicMock())
            assert isinstance(result, OTelExporter)
