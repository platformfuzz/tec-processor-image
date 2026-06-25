"""Unit tests for DynamoDB job status update functions in logic.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from processor.logic import safe_update_job_status, update_job_status


class TestUpdateJobStatus:
    """Tests for update_job_status."""

    def test_skips_when_table_name_is_none(self):
        """Should return immediately when table_name is None."""
        with patch("processor.logic.boto3") as mock_boto3:
            update_job_status(None, "job-123", "processing")
            mock_boto3.client.assert_not_called()

    def test_skips_when_table_name_is_empty(self):
        """Should return immediately when table_name is empty string."""
        with patch("processor.logic.boto3") as mock_boto3:
            update_job_status("", "job-123", "processing")
            mock_boto3.client.assert_not_called()

    def test_skips_when_job_id_is_none(self):
        """Should return immediately when job_id is None."""
        with patch("processor.logic.boto3") as mock_boto3:
            update_job_status("my-table", None, "processing")
            mock_boto3.client.assert_not_called()

    def test_skips_when_job_id_is_empty(self):
        """Should return immediately when job_id is empty string."""
        with patch("processor.logic.boto3") as mock_boto3:
            update_job_status("my-table", "", "processing")
            mock_boto3.client.assert_not_called()

    def test_updates_processing_status(self):
        """Should call DynamoDB update_item with processing status."""
        mock_ddb = MagicMock()
        with patch("processor.logic.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ddb
            update_job_status("my-table", "job-123", "processing")

        mock_ddb.update_item.assert_called_once()
        call_kwargs = mock_ddb.update_item.call_args[1]
        assert call_kwargs["TableName"] == "my-table"
        assert call_kwargs["Key"] == {"job_id": {"S": "job-123"}}
        assert ":status" in call_kwargs["ExpressionAttributeValues"]
        assert call_kwargs["ExpressionAttributeValues"][":status"] == {"S": "processing"}
        assert ":updated_at" in call_kwargs["ExpressionAttributeValues"]

    def test_updates_completed_with_output_key(self):
        """Should include output_key in update expression on completed."""
        mock_ddb = MagicMock()
        with patch("processor.logic.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ddb
            update_job_status(
                "my-table", "job-123", "completed",
                output_key="processed/station=auck/year=2024/doy=150/auck1500.parquet",
            )

        call_kwargs = mock_ddb.update_item.call_args[1]
        assert call_kwargs["ExpressionAttributeValues"][":status"] == {"S": "completed"}
        assert ":output_key" in call_kwargs["ExpressionAttributeValues"]
        assert call_kwargs["ExpressionAttributeValues"][":output_key"] == {
            "S": "processed/station=auck/year=2024/doy=150/auck1500.parquet"
        }
        assert "output_key = :output_key" in call_kwargs["UpdateExpression"]

    def test_updates_failed_with_error_fields(self):
        """Should include error_type and error_message on failed."""
        mock_ddb = MagicMock()
        with patch("processor.logic.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ddb
            update_job_status(
                "my-table", "job-123", "failed",
                error_type="CalibrationError",
                error_message="No valid TEC rows",
            )

        call_kwargs = mock_ddb.update_item.call_args[1]
        assert call_kwargs["ExpressionAttributeValues"][":status"] == {"S": "failed"}
        assert call_kwargs["ExpressionAttributeValues"][":error_type"] == {"S": "CalibrationError"}
        assert call_kwargs["ExpressionAttributeValues"][":error_message"] == {"S": "No valid TEC rows"}
        assert "error_type = :error_type" in call_kwargs["UpdateExpression"]
        assert "error_message = :error_message" in call_kwargs["UpdateExpression"]

    def test_updated_at_is_iso_format(self):
        """Should set updated_at to an ISO 8601 timestamp."""
        mock_ddb = MagicMock()
        with patch("processor.logic.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ddb
            update_job_status("my-table", "job-123", "processing")

        call_kwargs = mock_ddb.update_item.call_args[1]
        updated_at = call_kwargs["ExpressionAttributeValues"][":updated_at"]["S"]
        # Should match YYYY-MM-DDTHH:MM:SSZ pattern
        assert updated_at.endswith("Z")
        assert "T" in updated_at
        assert len(updated_at) == 20


class TestSafeUpdateJobStatus:
    """Tests for safe_update_job_status."""

    def test_skips_when_table_name_is_none(self):
        """Should return immediately when table_name is None."""
        with patch("processor.logic.boto3") as mock_boto3:
            safe_update_job_status(None, "job-123", "processing")
            mock_boto3.client.assert_not_called()

    def test_skips_when_job_id_is_none(self):
        """Should return immediately when job_id is None."""
        with patch("processor.logic.boto3") as mock_boto3:
            safe_update_job_status("my-table", None, "processing")
            mock_boto3.client.assert_not_called()

    def test_skips_when_job_id_is_empty(self):
        """Should return immediately when job_id is empty."""
        with patch("processor.logic.boto3") as mock_boto3:
            safe_update_job_status("my-table", "", "processing")
            mock_boto3.client.assert_not_called()

    def test_calls_update_job_status_on_success(self):
        """Should delegate to update_job_status when inputs are valid."""
        mock_ddb = MagicMock()
        with patch("processor.logic.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ddb
            safe_update_job_status("my-table", "job-123", "processing", trace_id="trace-abc")

        mock_ddb.update_item.assert_called_once()

    def test_logs_warning_on_ddb_error(self, capsys):
        """Should log warning with outcome=ddb_update_warning on DDB error."""
        mock_ddb = MagicMock()
        mock_ddb.update_item.side_effect = Exception("ConditionalCheckFailed")
        with patch("processor.logic.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ddb
            # Should not raise
            safe_update_job_status(
                "my-table", "job-123", "processing",
                trace_id="trace-abc",
            )

        captured = capsys.readouterr()
        log_entry = json.loads(captured.out.strip())
        assert log_entry["outcome"] == "ddb_update_warning"
        assert log_entry["trace_id"] == "trace-abc"
        assert log_entry["job_id"] == "job-123"
        assert log_entry["error_type"] == "Exception"
        assert "ConditionalCheckFailed" in log_entry["error_message"]

    def test_does_not_raise_on_ddb_error(self):
        """Should never raise even when DynamoDB update fails."""
        mock_ddb = MagicMock()
        mock_ddb.update_item.side_effect = RuntimeError("DDB unavailable")
        with patch("processor.logic.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ddb
            # This should not raise
            safe_update_job_status("my-table", "job-123", "failed")

    def test_uses_unknown_trace_id_when_none(self, capsys):
        """Should use 'unknown' as trace_id when not provided."""
        mock_ddb = MagicMock()
        mock_ddb.update_item.side_effect = Exception("Error")
        with patch("processor.logic.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ddb
            safe_update_job_status("my-table", "job-123", "processing")

        captured = capsys.readouterr()
        log_entry = json.loads(captured.out.strip())
        assert log_entry["trace_id"] == "unknown"

    def test_passes_kwargs_to_update_job_status(self):
        """Should forward output_key, error_type, error_message to update_job_status."""
        mock_ddb = MagicMock()
        with patch("processor.logic.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_ddb
            safe_update_job_status(
                "my-table", "job-123", "completed",
                trace_id="trace-abc",
                output_key="processed/test.parquet",
            )

        call_kwargs = mock_ddb.update_item.call_args[1]
        assert call_kwargs["ExpressionAttributeValues"][":output_key"] == {"S": "processed/test.parquet"}
