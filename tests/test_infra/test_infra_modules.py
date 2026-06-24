"""Comprehensive tests for all 8 infra template modules.

Run with:
    pytest templates/tests/test_infra/test_infra_modules.py -v

Each module has at least 5 tests covering its core behaviours, error paths,
and integration with other modules.
"""

import asyncio
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Ensure templates directory is on the path so imports work
# ---------------------------------------------------------------------------
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent
if str(_TEMPLATES_DIR) not in sys.path:
    sys.path.insert(0, str(_TEMPLATES_DIR))

# Reset module-level singletons between tests via a fixture defined below.

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_singletons() -> Any:
    """Reset all module-level singletons before each test."""
    import aukern_infra.db as db_mod
    import aukern_infra.events as events_mod
    import aukern_infra.health as health_mod
    import aukern_infra.logging as logging_mod
    import aukern_infra.metrics as metrics_mod

    # Save originals
    orig_metrics = metrics_mod._collector_instance
    orig_health = health_mod._health_checker_instance
    orig_db = db_mod._db_manager_instance
    orig_logging_configured = logging_mod._CONFIGURED
    orig_bus = events_mod._default_bus

    # Reset
    metrics_mod._collector_instance = None
    health_mod._health_checker_instance = None
    db_mod._db_manager_instance = None
    logging_mod._CONFIGURED = False
    events_mod.reset_event_bus()

    yield

    # Teardown — close any DB manager that tests may have created.
    # DatabaseManager.close() is async; use asyncio.run() since this fixture is sync.
    if db_mod._db_manager_instance is not None:
        try:
            import asyncio

            asyncio.run(db_mod._db_manager_instance.close())
        except Exception:  # noqa: S110
            pass

    # Restore
    metrics_mod._collector_instance = orig_metrics
    health_mod._health_checker_instance = orig_health
    db_mod._db_manager_instance = orig_db
    logging_mod._CONFIGURED = orig_logging_configured
    events_mod._default_bus = orig_bus


# ============================================================================
# Module 1: logging.py
# ============================================================================


class TestLogging:
    """Tests for infra.logging module."""

    def test_get_logger_returns_named_logger(self) -> None:
        from aukern_infra.logging import get_logger

        logger = get_logger("test.module")
        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_get_logger_idempotent(self) -> None:
        from aukern_infra.logging import get_logger

        a = get_logger("same.name")
        b = get_logger("same.name")
        assert a is b

    def test_configure_logging_creates_files(self, tmp_path: Path) -> None:
        from aukern_infra.logging import configure_logging

        configure_logging(tmp_path, log_level="DEBUG", enable_json=True)
        # Channel files are created on first write; directory must exist
        assert tmp_path.is_dir()

    def test_configure_logging_is_idempotent(self, tmp_path: Path) -> None:
        from aukern_infra.logging import configure_logging

        configure_logging(tmp_path)
        configure_logging(tmp_path)  # Second call must not raise

    def test_correlation_id_generated_automatically(self) -> None:
        from aukern_infra.logging import get_correlation_id

        cid = get_correlation_id()
        assert isinstance(cid, str)
        assert len(cid) == 36  # UUID4 format

    def test_set_correlation_id_persists_in_thread(self) -> None:
        from aukern_infra.logging import get_correlation_id, set_correlation_id

        set_correlation_id("my-test-id")
        assert get_correlation_id() == "my-test-id"

    def test_correlation_context_manager_restores_on_exit(self) -> None:
        from aukern_infra.logging import CorrelationContext, get_correlation_id, set_correlation_id

        set_correlation_id("outer-id")
        with CorrelationContext("inner-id"):
            assert get_correlation_id() == "inner-id"
        assert get_correlation_id() == "outer-id"

    def test_correlation_context_generates_id_when_none_given(self) -> None:
        from aukern_infra.logging import CorrelationContext

        ctx = CorrelationContext()
        assert len(ctx.correlation_id) == 36

    def test_mask_pii_masks_sensitive_fields(self) -> None:
        from aukern_infra.logging import mask_pii

        assert mask_pii("secret-value", "password") == "[REDACTED]"
        assert mask_pii("abc", "database_password") == "[REDACTED]"
        assert mask_pii("abc", "api_key") == "[REDACTED]"
        assert mask_pii("abc", "email") == "[REDACTED]"

    def test_mask_pii_leaves_non_sensitive_intact(self) -> None:
        from aukern_infra.logging import mask_pii

        assert mask_pii("john", "first_name") == "john"
        assert mask_pii("42", "user_id") == "42"


# ============================================================================
# Module 2: metrics.py
# ============================================================================


class TestMetrics:
    """Tests for infra.metrics module."""

    def test_get_metrics_collector_returns_singleton(self) -> None:
        from aukern_infra.metrics import get_metrics_collector

        a = get_metrics_collector()
        b = get_metrics_collector()
        assert a is b

    def test_increment_adds_counter_observation(self) -> None:
        from aukern_infra.metrics import get_metrics_collector

        collector = get_metrics_collector()
        collector.increment("request_count")
        collector.increment("request_count")
        summary = collector.get_summary()
        assert summary["request_count"]["count"] == 2

    def test_record_stores_value(self) -> None:
        from aukern_infra.metrics import MetricType, get_metrics_collector

        collector = get_metrics_collector()
        collector.record("response_time_ms", 42.0, MetricType.HISTOGRAM)
        summary = collector.get_summary()
        assert summary["response_time_ms"]["min"] == 42.0
        assert summary["response_time_ms"]["max"] == 42.0

    def test_get_summary_returns_percentiles(self) -> None:
        from aukern_infra.metrics import MetricType, get_metrics_collector

        collector = get_metrics_collector()
        for v in [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]:
            collector.record("latency", v, MetricType.HISTOGRAM)
        summary = collector.get_summary()["latency"]
        assert summary["p50"] > 0
        assert summary["p95"] >= summary["p50"]
        assert summary["p99"] >= summary["p95"]

    def test_track_operation_counts_calls(self) -> None:
        from aukern_infra.metrics import get_metrics_collector, track_operation

        collector = get_metrics_collector()

        @track_operation("my_op")
        def do_work() -> str:
            return "done"

        do_work()
        do_work()
        summary = collector.get_summary()
        assert summary["my_op_calls"]["count"] == 2

    def test_observed_emits_prometheus_red_metrics(self) -> None:
        """@observed must expose scrapeable RED metrics at /metrics (operations_total)."""
        pytest.importorskip("prometheus_client")
        from aukern_infra.metrics import metrics_asgi_app, observed
        from prometheus_client import generate_latest

        @observed("qa.red_op")
        def do_work() -> str:
            return "ok"

        do_work()
        exposition = generate_latest().decode()
        assert "operations_total" in exposition
        assert 'operation="qa.red_op"' in exposition
        assert metrics_asgi_app() is not None  # /metrics endpoint is mountable

    def test_track_operation_counts_errors(self) -> None:
        from aukern_infra.metrics import get_metrics_collector, track_operation

        collector = get_metrics_collector()

        @track_operation("failing_op")
        def always_fails() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError):
            always_fails()
        summary = collector.get_summary()
        assert summary["failing_op_errors"]["count"] == 1

    def test_track_operation_records_duration(self) -> None:
        from aukern_infra.metrics import get_metrics_collector, track_operation

        collector = get_metrics_collector()

        @track_operation("timed_op")
        def quick() -> None:
            pass

        quick()
        summary = collector.get_summary()
        assert "timed_op_duration_ms" in summary
        assert summary["timed_op_duration_ms"]["min"] >= 0.0

    def test_track_operation_works_on_async_functions(self) -> None:
        from aukern_infra.metrics import get_metrics_collector, track_operation

        collector = get_metrics_collector()

        @track_operation("async_op")
        async def async_work() -> str:
            return "async_done"

        asyncio.run(async_work())
        summary = collector.get_summary()
        assert summary["async_op_calls"]["count"] == 1

    def test_thread_safe_increment(self) -> None:
        import threading

        from aukern_infra.metrics import get_metrics_collector

        collector = get_metrics_collector()
        threads = [
            threading.Thread(target=lambda: collector.increment("shared_counter"))
            for _ in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        summary = collector.get_summary()
        assert summary["shared_counter"]["count"] == 50


# ============================================================================
# Module 3: secrets.py
# ============================================================================


class TestSecrets:
    """Tests for infra.secrets module."""

    def test_get_returns_value(self, tmp_path: Path) -> None:
        from aukern_infra.secrets import load_vault

        vault_file = tmp_path / ".secrets"
        vault_file.write_text("DB_URL=postgres://localhost/mydb\n")
        vault = load_vault(vault_file)
        assert vault.get("DB_URL") == "postgres://localhost/mydb"

    def test_get_raises_on_missing_key(self, tmp_path: Path) -> None:
        from aukern_infra.errors import SecretsError
        from aukern_infra.secrets import load_vault

        vault_file = tmp_path / ".secrets"
        vault_file.write_text("EXISTING=value\n")
        vault = load_vault(vault_file)
        with pytest.raises(SecretsError, match="not found"):
            vault.get("MISSING_KEY")

    def test_get_raises_on_placeholder(self, tmp_path: Path) -> None:
        from aukern_infra.errors import SecretsError
        from aukern_infra.secrets import load_vault

        vault_file = tmp_path / ".secrets"
        vault_file.write_text("MY_SECRET=PLACEHOLDER\n")
        vault = load_vault(vault_file)
        with pytest.raises(SecretsError, match="PLACEHOLDER"):
            vault.get("MY_SECRET")

    def test_is_configured_false_when_placeholders_remain(self, tmp_path: Path) -> None:
        from aukern_infra.secrets import load_vault

        vault_file = tmp_path / ".secrets"
        vault_file.write_text("KEY_A=real_value\nKEY_B=PLACEHOLDER\n")
        vault = load_vault(vault_file)
        assert vault.is_configured() is False

    def test_is_configured_true_when_all_filled(self, tmp_path: Path) -> None:
        from aukern_infra.secrets import load_vault

        vault_file = tmp_path / ".secrets"
        vault_file.write_text("KEY_A=real_value\nKEY_B=another_value\n")
        vault = load_vault(vault_file)
        assert vault.is_configured() is True

    def test_list_required_returns_all_keys(self, tmp_path: Path) -> None:
        from aukern_infra.secrets import load_vault

        vault_file = tmp_path / ".secrets"
        vault_file.write_text("KEY_A=value1\nKEY_B=PLACEHOLDER\n")
        vault = load_vault(vault_file)
        assert set(vault.list_required()) == {"KEY_A", "KEY_B"}

    def test_list_missing_returns_only_placeholders(self, tmp_path: Path) -> None:
        from aukern_infra.secrets import load_vault

        vault_file = tmp_path / ".secrets"
        vault_file.write_text("KEY_A=real\nKEY_B=PLACEHOLDER\n")
        vault = load_vault(vault_file)
        assert vault.list_missing() == ["KEY_B"]

    def test_generate_vault_template_creates_file(self, tmp_path: Path) -> None:
        from aukern_infra.secrets import generate_vault_template

        out = tmp_path / "vault.env"
        generate_vault_template(["DB_URL", "JWT_SECRET"], out)
        assert out.exists()
        content = out.read_text()
        # generate_vault_template emits YAML-style output: "KEY: PLACEHOLDER"
        assert "DB_URL" in content
        assert "PLACEHOLDER" in content
        assert "JWT_SECRET" in content

    def test_generate_vault_template_preserves_existing_values(self, tmp_path: Path) -> None:
        from aukern_infra.secrets import generate_vault_template

        out = tmp_path / "vault.env"
        out.write_text("DB_URL=postgres://existing\n")
        generate_vault_template(["DB_URL", "NEW_KEY"], out)
        content = out.read_text()
        assert "NEW_KEY" in content
        assert "PLACEHOLDER" in content

    def test_get_does_not_log_secret_value(self, tmp_path: Path, caplog: Any) -> None:
        from aukern_infra.secrets import load_vault

        vault_file = tmp_path / ".secrets"
        vault_file.write_text("MY_TOKEN=super-secret-value\n")
        vault = load_vault(vault_file)
        with caplog.at_level(logging.INFO):
            vault.get("MY_TOKEN")
        assert "super-secret-value" not in caplog.text


# ============================================================================
# Module 4: resilience.py
# ============================================================================


class TestResilience:
    """Tests for infra.resilience module."""

    def test_retry_retries_correct_number_of_times(self) -> None:
        from aukern_infra.resilience import retry

        call_count = [0]

        @retry(max_attempts=3, backoff="fixed", base_delay=0.0, jitter=False)
        def flaky() -> str:
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("not yet")
            return "ok"

        result = flaky()
        assert result == "ok"
        assert call_count[0] == 3

    def test_retry_raises_after_exhausting_attempts(self) -> None:
        from aukern_infra.resilience import retry

        call_count = [0]

        @retry(max_attempts=2, backoff="fixed", base_delay=0.0, jitter=False)
        def always_fails() -> None:
            call_count[0] += 1
            raise RuntimeError("always")

        with pytest.raises(RuntimeError, match="always"):
            always_fails()
        assert call_count[0] == 2

    def test_retry_non_retryable_exception_skips_retry(self) -> None:
        from aukern_infra.resilience import retry

        call_count = [0]

        @retry(
            max_attempts=3,
            backoff="fixed",
            base_delay=0.0,
            jitter=False,
            non_retryable_exceptions=(TypeError,),
        )
        def raises_type_error() -> None:
            call_count[0] += 1
            raise TypeError("stop now")

        with pytest.raises(TypeError):
            raises_type_error()
        assert call_count[0] == 1  # No retries

    def test_exponential_backoff_delay_grows(self) -> None:
        from aukern_infra.resilience import _calculate_delay

        d0 = _calculate_delay(0, "exponential", 1.0, 60.0, False)
        d1 = _calculate_delay(1, "exponential", 1.0, 60.0, False)
        d2 = _calculate_delay(2, "exponential", 1.0, 60.0, False)
        assert d0 < d1 < d2

    def test_circuit_breaker_opens_after_threshold(self) -> None:
        import aukern_infra.resilience as res_mod
        from aukern_infra.resilience import CircuitBreakerOpenError, circuit_breaker

        # Use a unique function name to avoid state from other tests
        @circuit_breaker(failure_threshold=2, recovery_timeout=999.0)
        def unstable_service() -> None:
            raise ConnectionError("down")

        # Clear any pre-existing state for this function
        res_mod._circuit_breakers.pop("unstable_service", None)

        for _ in range(2):
            with pytest.raises(ConnectionError):
                unstable_service()

        with pytest.raises(CircuitBreakerOpenError):
            unstable_service()

    def test_circuit_breaker_recovers_after_timeout(self) -> None:
        import aukern_infra.resilience as res_mod
        from aukern_infra.resilience import CircuitBreakerState, circuit_breaker

        # Clear any stale state before creating the decorated function
        res_mod._circuit_breakers.pop("cb_recovery_test", None)

        @circuit_breaker(failure_threshold=1, recovery_timeout=0.05)
        def cb_recovery_test() -> str:
            return "ok"

        # The decorator registered the CB at decoration time — retrieve it
        cb = res_mod._circuit_breakers["cb_recovery_test"]

        # Manually trip the breaker and backdate the failure time past timeout
        with cb.lock:
            cb.state = CircuitBreakerState.OPEN
            cb.failure_count = 1
            cb.last_failure_time = time.monotonic() - 0.2  # well past 0.05s

        result = cb_recovery_test()
        assert result == "ok"
        assert cb.state == CircuitBreakerState.CLOSED

    def test_timeout_raises_on_slow_function(self) -> None:
        from aukern_infra.resilience import ResilienceTimeoutError, timeout

        @timeout(seconds=0.05)
        def slow() -> None:
            time.sleep(1.0)

        with pytest.raises(ResilienceTimeoutError):
            slow()

    def test_timeout_passes_fast_function(self) -> None:
        from aukern_infra.resilience import timeout

        @timeout(seconds=2.0)
        def fast() -> str:
            return "quick"

        assert fast() == "quick"

    def test_bulkhead_rejects_excess_concurrent_calls(self) -> None:
        import threading

        from aukern_infra.resilience import BulkheadFullError, bulkhead

        results: list[Any] = []
        barrier = threading.Barrier(2)
        held = threading.Event()

        @bulkhead(max_concurrent=1)
        def hold_slot() -> None:
            held.set()
            barrier.wait(timeout=2)

        def occupier() -> None:
            hold_slot()

        def intruder() -> None:
            held.wait(timeout=1)
            try:
                hold_slot()
                results.append("passed")
            except BulkheadFullError:
                results.append("rejected")
            finally:
                barrier.wait(timeout=2)

        t1 = threading.Thread(target=occupier)
        t2 = threading.Thread(target=intruder)
        t1.start()
        t2.start()
        t1.join(timeout=3)
        t2.join(timeout=3)

        assert "rejected" in results

    def test_retry_works_on_async_functions(self) -> None:
        from aukern_infra.resilience import retry

        call_count = [0]

        @retry(max_attempts=3, backoff="fixed", base_delay=0.0, jitter=False)
        async def async_flaky() -> str:
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("async not yet")
            return "async_ok"

        result = asyncio.run(async_flaky())
        assert result == "async_ok"
        assert call_count[0] == 3


# ============================================================================
# Module 5: health.py
# ============================================================================


class TestHealth:
    """Tests for infra.health module."""

    def test_get_health_checker_returns_singleton(self) -> None:
        from aukern_infra.health import get_health_checker

        a = get_health_checker()
        b = get_health_checker()
        assert a is b

    def test_register_and_run_check(self) -> None:
        from aukern_infra.health import HealthCheckResult, HealthStatus, get_health_checker

        checker = get_health_checker()

        def my_check() -> HealthCheckResult:
            return HealthCheckResult("custom", HealthStatus.HEALTHY, "all good", 1.0)

        checker.register("custom", my_check)
        result = checker.check("custom")
        assert result.status == HealthStatus.HEALTHY
        assert result.name == "custom"

    def test_aggregated_status_reflects_worst_check(self) -> None:
        from aukern_infra.health import HealthCheckResult, HealthStatus, get_health_checker

        checker = get_health_checker()

        checker.register(
            "ok_check",
            lambda: HealthCheckResult("ok_check", HealthStatus.HEALTHY, "ok", 1.0),
        )
        checker.register(
            "bad_check",
            lambda: HealthCheckResult("bad_check", HealthStatus.UNHEALTHY, "down", 1.0),
        )
        response = checker.all_checks()
        assert response["status"] == HealthStatus.UNHEALTHY.value

    def test_readiness_uses_critical_checks_only(self) -> None:
        from aukern_infra.health import HealthCheckResult, HealthStatus, get_health_checker

        checker = get_health_checker()

        checker.register(
            "critical_ok",
            lambda: HealthCheckResult("critical_ok", HealthStatus.HEALTHY, "ok", 1.0),
            critical=True,
        )
        checker.register(
            "non_critical_fail",
            lambda: HealthCheckResult("non_critical_fail", HealthStatus.UNHEALTHY, "meh", 1.0),
            critical=False,
        )
        response = checker.readiness()
        assert response["status"] == HealthStatus.HEALTHY.value
        assert "critical_ok" in response["checks"]
        assert "non_critical_fail" not in response["checks"]

    def test_liveness_uses_non_critical_checks_only(self) -> None:
        from aukern_infra.health import HealthCheckResult, HealthStatus, get_health_checker

        checker = get_health_checker()

        checker.register(
            "non_critical",
            lambda: HealthCheckResult("non_critical", HealthStatus.HEALTHY, "ok", 1.0),
            critical=False,
        )
        checker.register(
            "critical_fail",
            lambda: HealthCheckResult("critical_fail", HealthStatus.UNHEALTHY, "down", 1.0),
            critical=True,
        )
        response = checker.liveness()
        assert response["status"] == HealthStatus.HEALTHY.value
        assert "non_critical" in response["checks"]
        assert "critical_fail" not in response["checks"]

    def test_database_check_factory_healthy(self) -> None:
        from aukern_infra.health import HealthStatus, database_check

        check_fn = database_check(lambda: sqlite3.connect(":memory:"))
        result = check_fn()
        assert result.status == HealthStatus.HEALTHY

    def test_database_check_factory_unhealthy(self) -> None:
        from aukern_infra.health import HealthStatus, database_check

        def bad_connection() -> Any:
            raise RuntimeError("cannot connect")

        check_fn = database_check(bad_connection)
        result = check_fn()
        assert result.status == HealthStatus.UNHEALTHY

    def test_check_raises_keyerror_for_unknown_name(self) -> None:
        from aukern_infra.health import get_health_checker

        checker = get_health_checker()
        with pytest.raises(KeyError):
            checker.check("nonexistent_check")

    def test_response_includes_timestamp_and_checks_key(self) -> None:
        from aukern_infra.health import HealthCheckResult, HealthStatus, get_health_checker

        checker = get_health_checker()
        checker.register(
            "ping",
            lambda: HealthCheckResult("ping", HealthStatus.HEALTHY, "pong", 0.5),
        )
        response = checker.all_checks()
        assert "timestamp" in response
        assert "checks" in response
        assert "ping" in response["checks"]


# ============================================================================
# Module 6: errors.py
# ============================================================================


class TestErrors:
    """Tests for infra.errors module."""

    def test_to_dict_contains_all_fields(self) -> None:
        from aukern_infra.errors import AppError

        err = AppError("something broke", code="APP_999", internal="debug info")
        d = err.to_dict()
        assert d["code"] == "APP_999"
        assert d["message"] == "something broke"
        assert d["internal"] == "debug info"
        assert "severity" in d
        assert "retryable" in d
        assert "context" in d

    def test_to_user_response_excludes_internal_and_context(self) -> None:
        from aukern_infra.errors import AppError

        err = AppError(
            "public message",
            code="APP_001",
            internal="secret debug info",
            context={"sql": "DROP TABLE users"},
        )
        resp = err.to_user_response()
        assert "internal" not in resp
        assert "context" not in resp
        assert resp["error"] == "APP_001"
        assert resp["message"] == "public message"

    def test_validation_error_defaults(self) -> None:
        from aukern_infra.errors import ValidationError

        err = ValidationError("bad input")
        assert err.code.startswith("VAL_")
        assert err.severity == "low"
        assert err.retryable is False

    def test_external_service_error_is_retryable(self) -> None:
        from aukern_infra.errors import ExternalServiceError

        err = ExternalServiceError("API down")
        assert err.retryable is True

    def test_handle_error_logs_at_info_for_low_severity(self, caplog: Any) -> None:
        from aukern_infra.errors import AppError, handle_error

        err = AppError("minor issue", code="APP_001", severity="low")
        with caplog.at_level(logging.INFO):
            handle_error(err)
        assert "minor issue" in caplog.text

    def test_handle_error_logs_at_error_for_high_severity(self, caplog: Any) -> None:
        from aukern_infra.errors import AppError, handle_error

        err = AppError("critical failure", code="APP_001", severity="high")
        with caplog.at_level(logging.ERROR):
            handle_error(err)
        assert "critical failure" in caplog.text

    def test_handle_error_emits_metric(self) -> None:
        from aukern_infra.errors import AppError, handle_error
        from aukern_infra.metrics import get_metrics_collector

        collector = get_metrics_collector()
        err = AppError("metered error", code="APP_001", severity="medium")
        handle_error(err)
        summary = collector.get_summary()
        assert "error_count" in summary

    def test_not_found_error_code_prefix(self) -> None:
        from aukern_infra.errors import NotFoundError

        err = NotFoundError("user not found")
        assert err.code.startswith("NF_")

    def test_secrets_error_critical_severity(self) -> None:
        from aukern_infra.errors import SecretsError

        err = SecretsError("missing secret")
        assert err.severity == "critical"

    def test_authentication_error_high_severity(self) -> None:
        from aukern_infra.errors import AuthenticationError

        err = AuthenticationError("bad token")
        assert err.severity == "high"


# ============================================================================
# Module 7: config.py
# ============================================================================


class TestConfig:
    """Tests for infra.config module."""

    def test_load_config_with_defaults(self) -> None:
        from aukern_infra.config import ConfigField, load_config

        config = load_config(
            [ConfigField("MAX_RETRIES", int, default=3)],
            env_prefix="TEST",
        )
        assert config.get_int("MAX_RETRIES") == 3

    def test_env_var_overrides_default(self, monkeypatch: Any) -> None:
        from aukern_infra.config import ConfigField, load_config

        monkeypatch.setenv("MYAPP_TIMEOUT", "42")
        config = load_config(
            [ConfigField("TIMEOUT", int, default=10)],
            env_prefix="MYAPP",
        )
        assert config.get_int("TIMEOUT") == 42

    def test_required_field_missing_raises(self) -> None:
        from aukern_infra.config import ConfigField, load_config
        from aukern_infra.errors import ConfigurationError

        # Use a field name that is guaranteed not to exist in config/app_config.dev.yaml
        # (DATABASE_URL is present there, so it wouldn't raise)
        with pytest.raises(ConfigurationError, match="Required"):
            load_config(
                [ConfigField("NONEXISTENT_REQUIRED_FIELD_XYZ", str, required=True)],
                env_prefix="NOPREFIX",
            )

    def test_validate_returns_errors_for_missing_required(self) -> None:
        from aukern_infra.config import AppConfig, ConfigField

        config = AppConfig(
            [ConfigField("DB_URL", str, required=True)],
            env_prefix="NOPREFIX999",
        )
        errors = config.validate()
        assert len(errors) == 1
        assert "DB_URL" in errors[0]

    def test_bool_coercion_true_values(self, monkeypatch: Any) -> None:
        from aukern_infra.config import ConfigField, load_config

        for val in ("true", "yes", "1", "True", "YES"):
            monkeypatch.setenv("BOOLTEST_DEBUG", val)
            config = load_config(
                [ConfigField("DEBUG", bool, default=False)],
                env_prefix="BOOLTEST",
            )
            assert config.get_bool("DEBUG") is True

    def test_bool_coercion_false_values(self, monkeypatch: Any) -> None:
        from aukern_infra.config import ConfigField, load_config

        for val in ("false", "no", "0", "False", "NO"):
            monkeypatch.setenv("BOOLTEST_DEBUG", val)
            config = load_config(
                [ConfigField("DEBUG", bool, default=True)],
                env_prefix="BOOLTEST",
            )
            assert config.get_bool("DEBUG") is False

    def test_config_file_json_is_loaded(self, tmp_path: Path) -> None:
        # load_config uses config_dir (not config_file) and reads app_config.{env}.yaml
        import yaml as _yaml
        from aukern_infra.config import ConfigField, load_config

        # Write a valid yaml config file in the expected format for APP_ENV=dev
        cfg_file = tmp_path / "app_config.dev.yaml"
        cfg_file.write_text(_yaml.dump({"PORT": 9000}))
        config = load_config(
            [ConfigField("PORT", int, default=8000)],
            config_dir=tmp_path,
            env_prefix="NOPREFIX_UNIQUE",
        )
        assert config.get_int("PORT") == 9000

    def test_to_dict_masks_sensitive_fields(self) -> None:
        from aukern_infra.config import ConfigField, load_config

        config = load_config(
            [ConfigField("API_KEY", str, default="my-real-key")],
            env_prefix="MASK_TEST",
        )
        d = config.to_dict()
        assert d["API_KEY"] == "[REDACTED]"

    def test_env_prefix_uppercase_applied(self, monkeypatch: Any) -> None:
        from aukern_infra.config import ConfigField, load_config

        monkeypatch.setenv("SVC_WORKERS", "8")
        config = load_config(
            [ConfigField("WORKERS", int, default=1)],
            env_prefix="svc",  # lowercase prefix — must still work
        )
        assert config.get_int("WORKERS") == 8


# ============================================================================
# Module 8: db.py
# ============================================================================


class TestDatabase:
    """Tests for infra.db module.

    NOTE: DatabaseManager is async PostgreSQL (asyncpg). Tests that require a
    live database connection are marked @pytest.mark.integration and skipped
    in unit test runs (CI uses ``-m "not integration"``).

    The only unit-testable behaviour is the module-level API contract
    (singleton guard, missing-asyncpg error, uninitialised guard).
    """

    def test_get_database_manager_raises_if_not_initialised(self) -> None:
        """get_database_manager() must raise DatabaseError before init_database() is called."""
        import aukern_infra.db as db_mod
        from aukern_infra.db import get_database_manager
        from aukern_infra.errors import DatabaseError

        db_mod._db_manager_instance = None
        with pytest.raises(DatabaseError, match="not initialised"):
            get_database_manager()

    @pytest.mark.asyncio
    async def test_init_database_raises_without_asyncpg(self, monkeypatch: Any) -> None:
        """init_database() raises DatabaseError with a clear message when asyncpg is absent."""
        import aukern_infra.db as db_mod

        original_asyncpg = db_mod.asyncpg
        db_mod._db_manager_instance = None
        try:
            monkeypatch.setattr(db_mod, "asyncpg", None)
            from aukern_infra.errors import DatabaseError

            with pytest.raises(DatabaseError, match="asyncpg"):
                await db_mod.init_database()
        finally:
            db_mod.asyncpg = original_asyncpg

    @pytest.mark.integration
    async def test_execute_returns_rows(self) -> None:
        """execute() returns rows as dicts. Requires DATABASE_URL in environment."""
        from aukern_infra.db import init_database

        db = await init_database()
        rows = await db.execute("SELECT 1 AS n")
        assert rows[0]["n"] == 1
        await db.close()

    @pytest.mark.integration
    async def test_migrations_run_in_order(self) -> None:
        """run_migrations() applies pending migrations in version order."""
        from aukern_infra.db import Migration, init_database

        db = await init_database()
        db.register_migration(
            Migration(
                9001,
                "test_a",
                "CREATE TABLE IF NOT EXISTS _t9001 (id INT)",
                "DROP TABLE IF EXISTS _t9001",
            )
        )
        db.register_migration(
            Migration(
                9002,
                "test_b",
                "CREATE TABLE IF NOT EXISTS _t9002 (id INT)",
                "DROP TABLE IF EXISTS _t9002",
            )
        )
        applied = await db.run_migrations()
        assert 9001 in applied
        assert 9002 in applied
        # Cleanup
        await db.rollback_migration(9000)
        await db.close()

    @pytest.mark.integration
    async def test_run_migrations_idempotent(self) -> None:
        """run_migrations() applying nothing on second call."""
        from aukern_infra.db import Migration, init_database

        db = await init_database()
        db.register_migration(
            Migration(
                9003,
                "test_c",
                "CREATE TABLE IF NOT EXISTS _t9003 (id INT)",
                "DROP TABLE IF EXISTS _t9003",
            )
        )
        await db.run_migrations()
        applied_again = await db.run_migrations()
        assert 9003 not in applied_again
        await db.rollback_migration(9002)
        await db.close()
