"""Testes unitários para o módulo de Telemetria e Dashboard (v0.8)."""

import os
import tempfile
import pytest
from src.telemetry.dashboard import TelemetryDashboard


class MockRiskManager:
    max_active_positions = 5


class MockExecutionClient:
    def __init__(self, positions=None):
        self.positions = positions or []
        self.total_realized_pnl_usd = 25.50
        self.total_gas_paid_usd = 1.20


def test_telemetry_blocked_tokens_buffering():
    dashboard = TelemetryDashboard(max_audit_logs=3)
    dashboard.record_blocked_token("TOKEN1", "Freeze authority active")
    dashboard.record_blocked_token("TOKEN2", "Whale holding 25%")
    dashboard.record_blocked_token("TOKEN3", "Dev holding 12%")
    dashboard.record_blocked_token("TOKEN4", "Soft rug detected")

    assert len(dashboard.blocked_events) == 3
    assert dashboard.blocked_events[0]["symbol"] == "TOKEN2"
    assert dashboard.blocked_events[-1]["symbol"] == "TOKEN4"


def test_telemetry_terminal_view_empty():
    dashboard = TelemetryDashboard()
    client = MockExecutionClient(positions=[])
    risk = MockRiskManager()

    view = dashboard.format_terminal_view(client, risk)
    assert "ASYMMETRIC MOMENTUM ENGINE (v0.8)" in view
    assert "SLOTS: 0/5" in view
    assert "Nenhuma posição aberta" in view


def test_telemetry_terminal_view_with_positions():
    dashboard = TelemetryDashboard()
    dashboard.record_blocked_token("SCAM", "Dev > 10%")
    positions = [
        {
            "symbol": "ALPHA",
            "opened_at": 1000.0,
            "entry_price": 0.001,
            "current_price": 0.002,
            "current_multiplier": 2.0,
            "peak_multiplier": 2.1,
            "stop_multiplier": 1.4,
            "unrealized_pnl_usd": 15.0,
            "is_risk_free": True,
            "executed_targets": {2.0},
        }
    ]
    client = MockExecutionClient(positions=positions)
    risk = MockRiskManager()

    view = dashboard.format_terminal_view(client, risk)
    assert "ALPHA" in view
    assert "RISK-FREE" in view
    assert "SCAM" in view


def test_telemetry_generate_html(tmp_path):
    dashboard = TelemetryDashboard()
    dashboard.record_blocked_token("BADCOIN", "Whale > 15%")
    positions = [
        {
            "symbol": "GEM",
            "opened_at": 1000.0,
            "entry_price": 0.0005,
            "current_price": 0.001,
            "current_multiplier": 2.0,
            "peak_multiplier": 2.0,
            "stop_multiplier": 1.4,
            "unrealized_pnl_usd": 14.85,
            "is_risk_free": False,
            "executed_targets": set(),
        }
    ]
    client = MockExecutionClient(positions=positions)
    out_file = str(tmp_path / "dashboard.html")

    res = dashboard.generate_html_dashboard(client, MockRiskManager(), output_path=out_file)
    assert os.path.exists(res)
    with open(res, "r", encoding="utf-8") as f:
        html_text = f.read()

    assert "GEM" in html_text
    assert "BADCOIN" in html_text
    assert "Telemetria v0.8" in html_text
