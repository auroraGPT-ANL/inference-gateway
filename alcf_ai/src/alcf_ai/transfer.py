from dataclasses import dataclass
from datetime import datetime

import globus_sdk

from .auth import STAGING_COLLECTION_ID, get_transfer_authorizer


class TransferError(Exception):
    """Transfer task failed."""


class TransferTimeout(TransferError):
    """Transfer task did not complete within the allowed time."""


@dataclass
class TransferResult:
    task_id: str
    bytes_transferred: int
    elapsed_seconds: float
    effective_gbps: float
    source_collection_id: str
    source_path: str
    destination_collection_id: str
    destination_path: str


def run_globus_transfer(
    source_collection_id: str,
    source_path: str,
    destination_collection_id: str,
    destination_path: str,
    *,
    timeout: int = 300,
    polling_interval: int = 5,
    verify_checksum: bool = False,
) -> TransferResult:
    """
    Submit a Globus Transfer task and block until it completes.
    """
    auth_collection_id = (
        source_collection_id
        if source_collection_id != STAGING_COLLECTION_ID
        else destination_collection_id
    )
    auth = get_transfer_authorizer(auth_collection_id)
    tc = globus_sdk.TransferClient(authorizer=auth)

    tdata = globus_sdk.TransferData(
        source_endpoint=source_collection_id,
        destination_endpoint=destination_collection_id,
        label=f"transfer {source_path}",
        verify_checksum=verify_checksum,
    )
    tdata.add_item(source_path, destination_path)

    submit_result = tc.submit_transfer(tdata)
    task_id = submit_result["task_id"]

    completed = tc.task_wait(
        task_id,
        timeout=timeout,
        polling_interval=max(1, polling_interval),
    )

    task = tc.get_task(task_id)
    status = task["status"]

    if not completed:
        raise TransferTimeout(f"Task {task_id} still {status} after {timeout}s")

    if status == "FAILED":
        fatal = task.get("fatal_error") or {}
        raise TransferError(
            f"Task {task_id} failed: "
            f"{fatal.get('description', task.get('nice_status', 'unknown error'))}"
        )

    if status != "SUCCEEDED":
        raise TransferError(f"Task {task_id} ended with unexpected status: {status}")

    bytes_transferred = task["bytes_transferred"]
    effective_bps = task.get("effective_bytes_per_second", 0)
    effective_gbps = effective_bps / 1e9

    request_time = datetime.fromisoformat(task["request_time"])
    completion_time = datetime.fromisoformat(task["completion_time"])
    elapsed = (completion_time - request_time).total_seconds()

    return TransferResult(
        task_id=task_id,
        bytes_transferred=bytes_transferred,
        elapsed_seconds=elapsed,
        effective_gbps=effective_gbps,
        source_collection_id=source_collection_id,
        source_path=source_path,
        destination_collection_id=destination_collection_id,
        destination_path=destination_path,
    )
